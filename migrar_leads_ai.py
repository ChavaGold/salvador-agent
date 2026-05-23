import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from anthropic import Anthropic

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
client = Anthropic(api_key=ANTHROPIC_API_KEY)
MODEL = "claude-haiku-4-5-20251001"

ETAPAS_VALIDAS = [
    "nuevo", "en_conversacion", "interesado", "calificado",
    "cita_agendada", "acepto_propuesta_cita", "visito",
    "negociacion", "venta_cerrada", "perdido", "cancelado"
]

conn = psycopg2.connect(
    dbname="crm", user="crm_user", password="CRMinmobiliario2026",
    host="proyecto_piloto_agente_inmobiliario", port=5432
)
cur = conn.cursor(cursor_factory=RealDictCursor)

# Clientes unicos
cur.execute("""
    SELECT DISTINCT sender_id
    FROM conversaciones
    WHERE rol = 'user' AND sender_id IS NOT NULL AND sender_id != ''
""")
clientes = [r["sender_id"] for r in cur.fetchall()]
print("Clientes unicos a procesar:", len(clientes))
print("-" * 50)

procesados = 0
errores = 0

for sender in clientes:
    telefono = sender.split("@")[0] if "@" in sender else sender

    # Traer historial completo de este cliente
    cur.execute("""
        SELECT rol, mensaje, fecha
        FROM conversaciones
        WHERE sender_id = %s
        ORDER BY fecha ASC
    """, (sender,))
    msgs = cur.fetchall()

    # Construir texto del historial
    historial = ""
    for m in msgs:
        quien = "CLIENTE" if m["rol"] == "user" else "SANDRA"
        historial += f"{quien}: {m['mensaje']}\n"

    # Truncar si es muy largo (proteger tokens)
    if len(historial) > 8000:
        historial = historial[:8000]

    prompt = f"""Analiza esta conversacion entre un cliente y Sandra (asistente de ventas inmobiliarias de Salvador Navarro Bienes Raices). Infiere los datos del cliente.

CONVERSACION:
{historial}

Responde UNICAMENTE con un objeto JSON (sin texto adicional, sin markdown) con estos campos:
- "nombre": el nombre del cliente si lo mencionó o si Sandra lo saludó por nombre, sino null
- "etapa": una de estas opciones segun el avance de la conversacion: {", ".join(ETAPAS_VALIDAS)}
- "caliente": true si mostró urgencia o intencion real de compra/cita, false si no
- "notas": resumen de 1-2 frases del interes/situacion del cliente

Reglas para etapa:
- "nuevo": apenas saludó, sin interes claro
- "en_conversacion": platica activa pero sin definir interes fuerte
- "interesado": preguntó por propiedades o precios
- "calificado": dio datos de credito (Infonavit, monto, etc)
- "cita_agendada": acordó una cita para visitar
- "perdido": dejó de responder o dijo que no le interesa"""

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        texto = resp.content[0].text.strip()
        # limpiar posibles fences
        texto = texto.replace("```json", "").replace("```", "").strip()
        datos = json.loads(texto)

        nombre = datos.get("nombre")
        etapa = datos.get("etapa", "en_conversacion")
        if etapa not in ETAPAS_VALIDAS:
            etapa = "en_conversacion"
        caliente = bool(datos.get("caliente", False))
        notas = datos.get("notas", "")

        cur.execute("""
            INSERT INTO leads (usuario_id, nombre, telefono, etapa, caliente, notas, fuente, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (usuario_id) DO UPDATE SET
                nombre = COALESCE(EXCLUDED.nombre, leads.nombre),
                etapa = EXCLUDED.etapa,
                caliente = EXCLUDED.caliente,
                notas = EXCLUDED.notas,
                updated_at = NOW()
        """, (sender, nombre, telefono, etapa, caliente, notas, "migracion_historica"))
        conn.commit()
        procesados += 1
        print(f"[OK] {telefono} | {nombre or 'sin nombre'} | {etapa} | caliente={caliente}")
    except Exception as e:
        errores += 1
        conn.rollback()
        print(f"[ERROR] {telefono}: {e}")

print("-" * 50)
print("Procesados:", procesados, "| Errores:", errores)
cur.execute("SELECT COUNT(*) AS total FROM leads")
print("Total leads en BD:", cur.fetchone()["total"])
conn.close()

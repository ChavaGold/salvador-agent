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

# Conexion a Chatwoot (origen)
cw = psycopg2.connect(
    dbname="izoltek", user="postgres", password="grybehmbe2bx2obpvq4n",
    host="izoltek_chatwoot-db", port=5432
)
cwcur = cw.cursor(cursor_factory=RealDictCursor)

# Conexion al agente (destino)
ag = psycopg2.connect(
    dbname="crm", user="crm_user", password="CRMinmobiliario2026",
    host="proyecto_piloto_agente_inmobiliario", port=5432
)
agcur = ag.cursor(cursor_factory=RealDictCursor)

# Traer TODOS los contactos
cwcur.execute("SELECT id, name, phone_number, identifier FROM contacts")
contactos = cwcur.fetchall()
print("Total contactos:", len(contactos))
print("-" * 50)

leads_ok = 0
msgs_copiados = 0
sin_telefono = 0
errores = 0

for ct in contactos:
    contact_id = ct["id"]
    nombre_cw = ct["name"]
    telefono = ct["phone_number"] or ct["identifier"] or ""
    telefono = telefono.replace("+", "").split("@")[0] if telefono else ""
    if not telefono:
        sin_telefono += 1
        continue

    usuario_id = ct["identifier"] or telefono

    # Traer todos los mensajes de este contacto
    cwcur.execute("""
        SELECT m.message_type, m.content, m.created_at
        FROM messages m
        JOIN conversations conv ON conv.id = m.conversation_id
        WHERE conv.contact_id = %s
          AND m.content IS NOT NULL AND m.content != ''
        ORDER BY m.created_at ASC
    """, (contact_id,))
    msgs = cwcur.fetchall()

    try:
        if not msgs:
            # Sin historial: lead nuevo basico
            nombre = nombre_cw
            etapa = "nuevo"
            caliente = False
            notas = "Contacto sin historial de mensajes en Chatwoot"
        else:
            historial = ""
            for m in msgs:
                quien = "CLIENTE" if m["message_type"] == 0 else "SANDRA"
                historial += f"{quien}: {m['content']}\n"
            if len(historial) > 8000:
                historial = historial[:8000]

            prompt = f"""Analiza esta conversacion entre un cliente y Sandra (asistente de ventas inmobiliarias de Salvador Navarro Bienes Raices). El nombre conocido del contacto es: {nombre_cw or 'desconocido'}.

CONVERSACION:
{historial}

Responde UNICAMENTE con JSON (sin markdown):
- "nombre": nombre real del cliente (usa el conocido si es valido, o el que aparezca en la conversacion, sino null)
- "etapa": una de: {", ".join(ETAPAS_VALIDAS)}
- "caliente": true si mostro urgencia/intencion real de compra o cita, sino false
- "notas": resumen 1-2 frases del interes/situacion

Reglas etapa: nuevo=solo saludo; en_conversacion=platica sin interes fuerte; interesado=pregunto propiedades/precios; calificado=dio datos credito; cita_agendada=acordo visita; perdido=no responde o rechazo."""

            resp = client.messages.create(
                model=MODEL, max_tokens=300,
                messages=[{"role": "user", "content": prompt}]
            )
            texto = resp.content[0].text.strip().replace("```json", "").replace("```", "").strip()
            datos = json.loads(texto)
            nombre = datos.get("nombre") or nombre_cw
            etapa = datos.get("etapa", "en_conversacion")
            if etapa not in ETAPAS_VALIDAS:
                etapa = "en_conversacion"
            caliente = bool(datos.get("caliente", False))
            notas = datos.get("notas", "")

        # Insertar/actualizar lead
        agcur.execute("""
            INSERT INTO leads (usuario_id, nombre, telefono, etapa, caliente, notas, fuente, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (usuario_id) DO UPDATE SET
                nombre = COALESCE(EXCLUDED.nombre, leads.nombre),
                etapa = EXCLUDED.etapa,
                caliente = EXCLUDED.caliente,
                notas = EXCLUDED.notas,
                updated_at = NOW()
        """, (usuario_id, nombre, telefono, etapa, caliente, notas, "chatwoot_whatsapp"))

        # Copiar mensajes a conversaciones
        for m in msgs:
            rol = "user" if m["message_type"] == 0 else "assistant"
            mid_sint = f"cw_{contact_id}_{int(m['created_at'].timestamp())}_{rol}"
            agcur.execute("""
                INSERT INTO conversaciones (sender_id, rol, mensaje, fecha, mid, canal, usuario_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (mid) DO NOTHING
            """, (usuario_id, rol, m["content"], m["created_at"], mid_sint, "whatsapp", usuario_id))
            msgs_copiados += 1

        ag.commit()
        leads_ok += 1
        print(f"[OK] {telefono} | {nombre or 'sin nombre'} | {etapa} | caliente={caliente} | msgs={len(msgs)}")
    except Exception as e:
        errores += 1
        ag.rollback()
        print(f"[ERROR] {telefono}: {e}")

print("-" * 50)
print(f"Leads: {leads_ok} | Mensajes copiados: {msgs_copiados} | Sin telefono: {sin_telefono} | Errores: {errores}")
agcur.execute("SELECT COUNT(*) AS t FROM leads")
print("Total leads en BD:", agcur.fetchone()["t"])
agcur.execute("SELECT COUNT(*) AS t FROM conversaciones")
print("Total mensajes en conversaciones:", agcur.fetchone()["t"])
cw.close()
ag.close()

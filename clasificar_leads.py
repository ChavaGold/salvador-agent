import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from anthropic import Anthropic

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
client = Anthropic(api_key=ANTHROPIC_API_KEY)
MODEL = "claude-haiku-4-5-20251001"

TIPOS_VALIDOS = ["lead_venta", "conocido", "otro_negocio", "ruido"]

ag = psycopg2.connect(
    dbname="crm", user="crm_user", password="CRMinmobiliario2026",
    host="proyecto_piloto_agente_inmobiliario", port=5432
)
cur = ag.cursor(cursor_factory=RealDictCursor)

# Traer todos los leads
cur.execute("SELECT usuario_id, nombre FROM leads")
leads = cur.fetchall()
print("Leads a clasificar:", len(leads))
print("-" * 50)

clasificados = 0
ruido_auto = 0
errores = 0

for lead in leads:
    usuario_id = lead["usuario_id"]
    nombre = lead["nombre"] or ""

    # Regla rapida: grupos y pruebas = ruido sin gastar API
    if "@g.us" in str(usuario_id) or "(GROUP)" in nombre.upper() or usuario_id == "123456" or "EVOLUTIONAPI" in nombre.upper() or "BIENES RAICES" in nombre.upper():
        cur.execute("UPDATE leads SET tipo_contacto=%s WHERE usuario_id=%s", ("ruido", usuario_id))
        ag.commit()
        ruido_auto += 1
        print(f"[RUIDO-AUTO] {nombre or usuario_id}")
        continue

    # Traer historial
    cur.execute("""
        SELECT rol, mensaje FROM conversaciones
        WHERE usuario_id=%s OR sender_id=%s
        ORDER BY fecha ASC
    """, (usuario_id, usuario_id))
    msgs = cur.fetchall()

    if not msgs:
        cur.execute("UPDATE leads SET tipo_contacto=%s WHERE usuario_id=%s", ("ruido", usuario_id))
        ag.commit()
        ruido_auto += 1
        print(f"[RUIDO-AUTO sin msgs] {nombre or usuario_id}")
        continue

    historial = ""
    for m in msgs:
        quien = "CLIENTE" if m["rol"] == "user" else "SANDRA"
        historial += f"{quien}: {m['mensaje']}\n"
    if len(historial) > 8000:
        historial = historial[:8000]

    prompt = f"""Eres un clasificador para Salvador Navarro Bienes Raices (inmobiliaria en Tlajomulco, Jalisco). Salvador ademas tiene otros negocios (tramites de documentos, IMSS, pensiones, AFORE). Clasifica a este contacto segun su conversacion.

Nombre del contacto: {nombre or 'desconocido'}

CONVERSACION:
{historial}

Responde UNICAMENTE con JSON (sin markdown):
- "tipo": uno de: lead_venta, conocido, otro_negocio, ruido
- "razon": frase corta explicando por que

Definiciones:
- "lead_venta": pregunta o muestra interes en COMPRAR/VER una propiedad, casa, terreno, credito hipotecario, Infonavit para casa, cita para visitar fraccionamiento. Es un cliente potencial de venta inmobiliaria.
- "conocido": amigo, familiar o trato personal/social SIN intencion de comprar propiedad.
- "otro_negocio": te contacta por tramites de documentos, IMSS, pensiones, AFORE, actas, constancias, etc. (NO bienes raices).
- "ruido": pruebas tecnicas, mensajes vacios, spam, o contactos sin proposito identificable."""

    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        texto = resp.content[0].text.strip().replace("```json", "").replace("```", "").strip()
        datos = json.loads(texto)
        tipo = datos.get("tipo", "ruido")
        if tipo not in TIPOS_VALIDOS:
            tipo = "ruido"
        razon = datos.get("razon", "")

        cur.execute("UPDATE leads SET tipo_contacto=%s WHERE usuario_id=%s", (tipo, usuario_id))
        ag.commit()
        clasificados += 1
        print(f"[{tipo.upper()}] {nombre or usuario_id} -> {razon}")
    except Exception as e:
        errores += 1
        ag.rollback()
        print(f"[ERROR] {usuario_id}: {e}")

print("-" * 50)
print(f"Clasificados por IA: {clasificados} | Ruido automatico: {ruido_auto} | Errores: {errores}")
print()
cur.execute("SELECT tipo_contacto, COUNT(*) AS n FROM leads GROUP BY tipo_contacto ORDER BY n DESC")
print("RESUMEN POR TIPO:")
for r in cur.fetchall():
    print(f"  {r['tipo_contacto']}: {r['n']}")
ag.close()

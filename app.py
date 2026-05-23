"""
Sandra V2.2 - Agente Inmobiliario y Asistente Personal de Salvador Navarro
Maneja dos modos:
  - MODO AGENTE: conversaciones con clientes potenciales de Castana
  - MODO ASISTENTE: conversaciones con Salvador (le da reportes, recibe notificaciones)

Cambios V2.2:
  - Pausa/reanuda manual por conversation_id
  - Comando "adelante Sandra" para reanudar desde Chatwoot
  - Eliminada notificacion spam de mensajes no clasificados
  - Endpoint /pausar-manual para workflow de pausa
  - Endpoint /reanudar-sandra para workflow de reanudacion
"""
from flask import Flask, request, jsonify
from anthropic import Anthropic
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import os
import re
from datetime import datetime, date, timedelta
import urllib.request
import time

app = Flask(__name__)

# ============================================================
# CONFIGURACION
# ============================================================
DATABASE = os.getenv("DATABASE_URL", "postgresql://crm_user:CRMinmobiliario2026@proyecto_piloto_agente_inmobiliario:5432/crm")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

client = Anthropic(api_key=ANTHROPIC_API_KEY)

# Telefono del numero de WhatsApp de Sandra (para identificacion)
SANDRA_PHONE = "5213310977722"

# URL de inventario general (para cuando no hay match en BD)
SITIO_PROPIEDADES_ALTERNATIVAS = "https://propiedades.salvadornavarrobienesraices.com"

# Palabras que marcan un lead como CALIENTE
PALABRAS_CALIENTE = [
    "hoy", "mañana", "manana", "ya voy", "voy en camino", "ahorita",
    "ahora", "en este momento", "ahora mismo", "voy para alla", "voy llegando",
    "estoy cerca", "ya llegue", "estoy afuera"
]


# ============================================================
# KIT DE FOTOS CASTAÑA
# ============================================================
EVOLUTION_URL = "http://izoltek_evolution-api:8080/message/sendMedia/Salvadornavarrobienesraices"
EVOLUTION_APIKEY = "429683C4C977415CAAFCCE10F7D57E11"

# La ñ se codifica como %C3%B1 en las URLs
FOTOS_CASTANA = [
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/1_sala.jpeg",
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/2_comedor.jpeg",
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/3_medio_ba%C3%B1o.jpeg",
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/4_Ba%C3%B1o.jpeg",
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/5_Panoramica_sala.jpeg",
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/6_Centro_de_entretenimiento.jpeg",
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/7_cocina.jpeg",
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/8_cocina_1.jpeg",
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/9_Patio.jpeg",
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/10_Recamara_Principal.jpeg",
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/11_Recamara_principal_2.jpeg",
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/12_Recamara_secundaria.jpeg",
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/13_Fachada_2.jpeg",
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/14_Fachada_1.jpeg",
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/15_Rutasde_camion.jpeg",
    "https://raw.githubusercontent.com/ChavaGold/salvador-agent/main/castana/16_Precios_depas.jpeg",
]


def enviar_kit_castana(numero):
    """Envia las 16 fotos de Castaña al cliente via Evolution API (sendMedia).
    numero: telefono normalizado, ej '5213312345678'"""
    numero = normalizar_telefono(numero)
    enviadas = 0
    for url in FOTOS_CASTANA:
        payload = json.dumps({
            "number": numero,
            "mediatype": "image",
            "media": url
        }).encode("utf-8")
        req = urllib.request.Request(
            EVOLUTION_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "apikey": EVOLUTION_APIKEY
            },
            method="POST"
        )
        try:
            urllib.request.urlopen(req, timeout=20)
            enviadas += 1
            time.sleep(1)  # evita saturar/baneo de WhatsApp
        except Exception as e:
            print(f"[KIT] Error enviando {url}: {e}")
    print(f"[KIT] Enviadas {enviadas}/{len(FOTOS_CASTANA)} fotos a {numero}")
    return enviadas


# =====================================================================
# UTILIDADES DE BD
# =====================================================================
def get_db_connection():
    try:
        return psycopg2.connect(DATABASE)
    except Exception as e:
        print(f"[BD] Error conexion: {e}")
        return None


def normalizar_telefono(usuario_id):
    if not usuario_id:
        return ""
    return str(usuario_id).replace("@s.whatsapp.net", "").replace("+", "").replace(" ", "").replace("-", "")


def es_admin(usuario_id):
    """Verifica si el numero es un admin (Salvador o jefe que usa Sandra)"""
    telefono = normalizar_telefono(usuario_id)
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute("SELECT nombre, rol FROM sandra_admins WHERE telefono = %s AND activo = TRUE", (telefono,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row  # Devuelve (nombre, rol) o None
    except Exception as e:
        print(f"[BD] Error verificando admin: {e}")
        return None


def esta_en_blacklist(usuario_id):
    telefono = normalizar_telefono(usuario_id)
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM sandra_blacklist WHERE telefono = %s", (telefono,))
        result = cur.fetchone() is not None
        cur.close()
        conn.close()
        return result
    except Exception as e:
        print(f"[BD] Error blacklist: {e}")
        return False


def sandra_esta_activa(conversation_id, usuario_id=None):
    """Verifica si Sandra esta activa para una conversacion.
    Busca primero por conversation_id, luego por usuario_id como fallback."""
    conn = get_db_connection()
    if not conn:
        return True
    try:
        cur = conn.cursor()
        # Primero buscar por conversation_id
        if conversation_id:
            cur.execute("SELECT activa FROM sandra_control WHERE conversation_id = %s", (str(conversation_id),))
            row = cur.fetchone()
            if row:
                cur.close()
                conn.close()
                return row[0]
        # Fallback: buscar por usuario_id
        if usuario_id:
            cur.execute("SELECT activa FROM sandra_control WHERE usuario_id = %s", (usuario_id,))
            row = cur.fetchone()
            if row:
                cur.close()
                conn.close()
                return row[0]
        cur.close()
        conn.close()
        return True  # Si no hay registro, Sandra esta activa
    except Exception as e:
        print(f"[BD] Error control: {e}")
        return True


def pausar_sandra_para(conversation_id, usuario_id=None, motivo="Salvador respondio manualmente"):
    """Pausa Sandra para una conversacion especifica"""
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO sandra_control (conversation_id, usuario_id, activa, pausada_at, pausada_motivo, updated_at)
            VALUES (%s, %s, FALSE, NOW(), %s, NOW())
            ON CONFLICT (conversation_id) DO UPDATE SET
                activa = FALSE, pausada_at = NOW(),
                pausada_motivo = %s, updated_at = NOW(),
                usuario_id = COALESCE(EXCLUDED.usuario_id, sandra_control.usuario_id)
        """, (str(conversation_id), usuario_id, motivo, motivo))
        conn.commit()
        cur.close()
        conn.close()
        print(f"[PAUSA] Sandra pausada para conv={conversation_id} usuario={usuario_id} motivo={motivo}")
        return True
    except Exception as e:
        print(f"[BD] Error pausar: {e}")
        return False


def reanudar_sandra_para(conversation_id):
    """Reactiva Sandra para una conversacion especifica"""
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE sandra_control
            SET activa = TRUE, updated_at = NOW(), pausada_motivo = 'Reanudada por Salvador'
            WHERE conversation_id = %s
        """, (str(conversation_id),))
        conn.commit()
        cur.close()
        conn.close()
        print(f"[REANUDA] Sandra reanudada para conv={conversation_id}")
        return True
    except Exception as e:
        print(f"[BD] Error reanudar: {e}")
        return False


def es_comando_reanudar(mensaje):
    """Detecta si el mensaje es 'adelante Sandra' (case insensitive)"""
    if not mensaje:
        return False
    return mensaje.strip().lower().startswith("adelante sandra")


def get_historial_conversacion(usuario_id, limite=20):
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT rol, mensaje, timestamp FROM conversaciones
            WHERE usuario_id = %s
            ORDER BY timestamp DESC LIMIT %s
        """, (usuario_id, limite))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return list(reversed([dict(r) for r in rows]))
    except Exception as e:
        print(f"[BD] Error historial: {e}")
        return []


def detectar_palabra_caliente(mensaje):
    """Devuelve True si el mensaje contiene palabras que indican urgencia inmediata"""
    if not mensaje:
        return False
    msg_lower = mensaje.lower()
    return any(palabra in msg_lower for palabra in PALABRAS_CALIENTE)


def registrar_kit_enviado(usuario_id):
    """Deja una marca en el historial para no reenviar el kit de fotos."""
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO conversaciones (usuario_id, rol, mensaje, timestamp)
            VALUES (%s, 'sandra', %s, NOW())
        """, (usuario_id, "[KIT_FOTOS_ENVIADO]"))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[BD] Error registrar kit: {e}")
        return False


def encolar_notificacion(destinatario, mensaje, tipo="info"):
    """Guarda una notificacion para que n8n la mande al admin"""
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO notificaciones_pendientes (destinatario, mensaje, tipo)
            VALUES (%s, %s, %s)
        """, (destinatario, mensaje, tipo))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[BD] Error encolar notif: {e}")
        return False


def registrar_cambio_etapa(usuario_id, etapa_nueva, etapa_anterior=None, cambiado_por="sandra", notas=None):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO lead_etapas_historial (usuario_id, etapa_anterior, etapa_nueva, cambiado_por, notas)
            VALUES (%s, %s, %s, %s, %s)
        """, (usuario_id, etapa_anterior, etapa_nueva, cambiado_por, notas))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[BD] Error registrar etapa: {e}")
        return False


# =====================================================================
# TOOLS PARA MODO AGENTE (cliente -> Sandra)
# =====================================================================
TOOLS_AGENTE = [
    {
        "name": "consultar_inventario",
        "description": "Consulta propiedades disponibles. Sin parametros = desarrollo destacado (Castaña). Con zona o presupuesto = busca alternativas. SIEMPRE usar antes de cotizar precios.",
        "input_schema": {
            "type": "object",
            "properties": {
                "desarrollo": {"type": "string", "description": "Nombre del desarrollo (ej: Castaña, Rinconada San Alejandro). Vacio = todos."},
                "presupuesto_max": {"type": "number", "description": "Presupuesto maximo del cliente en pesos"},
                "zona": {"type": "string", "description": "Zona de preferencia (ej: Tlajomulco, Zapopan, Tonala)"},
                "tipo": {"type": "string", "description": "departamento_nuevo | casa_nueva | casa_usada | casa_recuperada"}
            },
            "required": []
        }
    },
    {
        "name": "evaluar_credito",
        "description": "Evalua si el credito del cliente alcanza para alguna propiedad del inventario. Devuelve opciones que le alcanzan y las que no.",
        "input_schema": {
            "type": "object",
            "properties": {
                "monto_credito": {"type": "number", "description": "Monto en pesos del credito"},
                "tipo_credito": {"type": "string", "description": "infonavit | fovissste | bancario | contado"},
                "zona_preferencia": {"type": "string", "description": "Zona preferida del cliente (opcional)"}
            },
            "required": ["monto_credito"]
        }
    },
    {
        "name": "agendar_visita",
        "description": "Agenda visita a cualquier desarrollo. Horarios: 9-11 AM o 2:30-6 PM. Si el cliente dice 'hoy', 'mañana', 'ya' o similar, agenda para ESE momento.",
        "input_schema": {
            "type": "object",
            "properties": {
                "usuario_id": {"type": "string"},
                "nombre_cliente": {"type": "string"},
                "desarrollo": {"type": "string", "description": "Nombre del desarrollo a visitar"},
                "fecha": {"type": "string", "description": "YYYY-MM-DD"},
                "hora": {"type": "string", "description": "HH:MM 24h"},
                "es_caliente": {"type": "boolean", "description": "True si es lead caliente (cita hoy/mañana inmediata)"},
                "necesita_traslado": {"type": "boolean"}
            },
            "required": ["usuario_id", "fecha", "hora"]
        }
    },
    {
        "name": "guardar_lead_update",
        "description": "Guarda o actualiza datos del lead (nombre, telefono, monto credito, etapa).",
        "input_schema": {
            "type": "object",
            "properties": {
                "usuario_id": {"type": "string"},
                "nombre": {"type": "string"},
                "telefono": {"type": "string"},
                "etapa": {"type": "string", "description": "nuevo_contacto | pidio_info | acepto_propuesta_cita | cita_agendada | no_califica | perdido"},
                "monto_credito": {"type": "number"},
                "tipo_credito": {"type": "string", "description": "infonavit | fovissste | bancario | contado"},
                "nivel_interes": {"type": "string", "description": "planta_baja | primer_nivel | segundo_nivel | usada"},
                "caliente": {"type": "boolean"},
                "notas": {"type": "string"}
            },
            "required": ["usuario_id"]
        }
    },
    {
        "name": "escalar_a_salvador",
        "description": "Pausa Sandra y notifica a Salvador. Usar cuando: lead caliente, cita agendada, tema complejo (Cofinavit, recuperar contrasena Infonavit), cliente pide humano.",
        "input_schema": {
            "type": "object",
            "properties": {
                "usuario_id": {"type": "string"},
                "motivo": {"type": "string", "description": "lead_caliente | cita_agendada | tema_complejo | pidio_humano"},
                "resumen": {"type": "string", "description": "1-2 lineas resumiendo al cliente para Salvador"},
                "urgente": {"type": "boolean", "description": "True si necesita atencion inmediata"}
            },
            "required": ["usuario_id", "motivo", "resumen"]
        }
    }
]


# =====================================================================
# TOOLS PARA MODO ASISTENTE (Salvador -> Sandra)
# =====================================================================
TOOLS_ASISTENTE = [
    {
        "name": "reporte_clientes_hoy",
        "description": "Cuantos clientes nuevos escribieron HOY, lista con nombres y telefonos.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "reporte_clientes_periodo",
        "description": "Reporte de clientes en un periodo. Usar cuando Salvador pide 'esta semana', 'este mes', 'ayer', etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "dias_atras": {"type": "integer", "description": "Cuantos dias hacia atras desde hoy. 1=ayer, 7=semana, 30=mes"}
            },
            "required": ["dias_atras"]
        }
    },
    {
        "name": "reporte_citas_agendadas",
        "description": "Lista de citas agendadas (proximas o de un dia especifico).",
        "input_schema": {
            "type": "object",
            "properties": {
                "rango": {"type": "string", "description": "hoy | manana | semana | todas"}
            },
            "required": ["rango"]
        }
    },
    {
        "name": "reporte_leads_calificados",
        "description": "Lista de leads CALIFICADOS pendientes de atender por Salvador.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "reporte_leads_calientes",
        "description": "Leads CALIENTES (los que pidieron cita inmediata o mostraron alta intencion).",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "buscar_lead_por_nombre",
        "description": "Busca un cliente por nombre y devuelve su info, etapa actual e historial.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre": {"type": "string", "description": "Nombre o parte del nombre del cliente"}
            },
            "required": ["nombre"]
        }
    },
    {
        "name": "resumen_dia",
        "description": "Resumen completo del dia: nuevos clientes, citas, calificados, leads calientes, perdidos.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "actualizar_etapa_lead",
        "description": "Salvador actualiza manualmente la etapa de un lead (cuando firmo, entrego expediente, etc).",
        "input_schema": {
            "type": "object",
            "properties": {
                "telefono_cliente": {"type": "string", "description": "Telefono del cliente"},
                "etapa_nueva": {"type": "string", "description": "Una de las 17 etapas del embudo"},
                "notas": {"type": "string"}
            },
            "required": ["telefono_cliente", "etapa_nueva"]
        }
    },
    {
        "name": "pausar_sandra_cliente",
        "description": "Pausar Sandra para un cliente especifico (Salvador toma control de esa conversacion).",
        "input_schema": {
            "type": "object",
            "properties": {
                "telefono_cliente": {"type": "string"}
            },
            "required": ["telefono_cliente"]
        }
    },
    {
        "name": "actualizar_precio_castana",
        "description": "Actualiza el precio de un nivel de Castana (planta baja, primer nivel, segundo nivel). Salvador puede decir 'el segundo nivel sube a 690000' y esto lo actualiza. Sandra cotizara con el precio nuevo automaticamente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nivel": {
                    "type": "string",
                    "description": "El nivel a actualizar: 'planta_baja', 'primer_nivel', 'segundo_nivel' (o el nombre exacto en BD)"
                },
                "precio_base": {
                    "type": "number",
                    "description": "Nuevo precio base (sin escrituras)"
                },
                "precio_con_escrituras": {
                    "type": "number",
                    "description": "Nuevo precio con escrituras incluidas. Si Salvador no lo dice, suma 20000 al precio_base."
                }
            },
            "required": ["nivel", "precio_base"]
        }
    },
    {
        "name": "ver_precios_castana",
        "description": "Muestra los precios actuales de todos los niveles de Castana.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    }
]


# =====================================================================
# IMPLEMENTACION TOOLS AGENTE
# =====================================================================
def tool_consultar_inventario(desarrollo=None, presupuesto_max=None, zona=None, tipo=None):
    """Consulta propiedades disponibles filtrando por desarrollo, zona, presupuesto o tipo"""
    conn = get_db_connection()
    if not conn:
        return {"error": "BD no disponible"}
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        conditions = ["activo = TRUE"]
        params = []

        if desarrollo:
            conditions.append("desarrollo ILIKE %s")
            params.append(f"%{desarrollo}%")
        if zona:
            conditions.append("zona ILIKE %s")
            params.append(f"%{zona}%")
        if presupuesto_max:
            conditions.append("precio_base <= %s")
            params.append(presupuesto_max)
        if tipo:
            conditions.append("tipo_propiedad ILIKE %s")
            params.append(f"%{tipo}%")

        where = " AND ".join(conditions)
        cur.execute(f"""
            SELECT desarrollo, zona, nivel, tipo_propiedad,
                   precio_base, precio_con_escrituras,
                   recamaras, banos_completos, medios_banos,
                   amenidades, tipo_credito_aceptado,
                   url_mapa, descripcion_corta, entrega, destacado
            FROM propiedades_castana
            WHERE {where}
            ORDER BY orden_prioridad ASC, precio_base ASC
        """, params)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()

        for r in rows:
            r["precio_base"] = float(r["precio_base"])
            r["precio_con_escrituras"] = float(r["precio_con_escrituras"])

        if not rows:
            return {
                "propiedades": [],
                "total": 0,
                "mensaje": "No hay propiedades que coincidan con esos criterios.",
                "url_inventario_general": SITIO_PROPIEDADES_ALTERNATIVAS,
                "instruccion": "Informa al cliente que no hay opciones con esos filtros. Comparte el link del inventario general y escala a Salvador."
            }

        # Agrupar por desarrollo
        desarrollos = {}
        for r in rows:
            d = r["desarrollo"]
            if d not in desarrollos:
                desarrollos[d] = {
                    "desarrollo": d,
                    "zona": r["zona"],
                    "url_mapa": r["url_mapa"],
                    "descripcion": r["descripcion_corta"],
                    "amenidades": r["amenidades"],
                    "creditos_aceptados": r["tipo_credito_aceptado"],
                    "entrega": r["entrega"],
                    "niveles": []
                }
            desarrollos[d]["niveles"].append({
                "nivel": r["nivel"],
                "precio_base": r["precio_base"],
                "precio_con_escrituras": r["precio_con_escrituras"],
                "recamaras": r["recamaras"],
                "banos": r["banos_completos"],
                "medios_banos": r["medios_banos"]
            })

        return {
            "propiedades": list(desarrollos.values()),
            "total": len(rows),
            "instruccion": "Presenta la ubicacion (con link de mapa), descripcion, amenidades, precios por nivel, y pregunta tipo de credito."
        }
    except Exception as e:
        return {"error": str(e)}


def tool_evaluar_credito(monto_credito, tipo_credito=None, zona_preferencia=None):
    """Evalua credito contra TODO el inventario"""
    conn = get_db_connection()
    if not conn:
        return {"error": "BD no disponible"}
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        conditions = ["activo = TRUE"]
        params = []
        if zona_preferencia:
            conditions.append("zona ILIKE %s")
            params.append(f"%{zona_preferencia}%")
        if tipo_credito:
            conditions.append("tipo_credito_aceptado ILIKE %s")
            params.append(f"%{tipo_credito}%")

        where = " AND ".join(conditions)
        cur.execute(f"""
            SELECT desarrollo, zona, nivel, precio_base, precio_con_escrituras,
                   url_mapa, descripcion_corta, amenidades, tipo_credito_aceptado
            FROM propiedades_castana
            WHERE {where}
            ORDER BY orden_prioridad ASC, precio_base ASC
        """, params)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()

        le_alcanzan = []
        no_le_alcanzan = []
        for r in rows:
            precio = float(r["precio_base"])
            r["precio_base"] = precio
            r["precio_con_escrituras"] = float(r["precio_con_escrituras"])
            if monto_credito >= precio:
                r["diferencia"] = monto_credito - precio
                le_alcanzan.append(r)
            else:
                r["falta"] = precio - monto_credito
                no_le_alcanzan.append(r)

        resultado = {
            "monto_credito": monto_credito,
            "tipo_credito": tipo_credito or "no_especificado",
            "le_alcanzan": le_alcanzan,
            "no_le_alcanzan": no_le_alcanzan[:3],
        }

        if le_alcanzan:
            resultado["califica_para"] = "si"
            resultado["instruccion"] = "Presenta las opciones que le alcanzan, destaca la mejor opcion y propón visita."
        else:
            cercana = no_le_alcanzan[0] if no_le_alcanzan else None
            falta = cercana["falta"] if cercana else 0
            resultado["califica_para"] = "no_alcanza"
            resultado["instruccion"] = f"No alcanza para ninguna propiedad. La más cercana necesita ${falta:,.0f} más. Ofrece Cofinavit o comparte link de inventario general."
            resultado["url_inventario_general"] = SITIO_PROPIEDADES_ALTERNATIVAS

        return resultado
    except Exception as e:
        return {"error": str(e)}


def tool_agendar_visita(usuario_id, fecha, hora, desarrollo="Castaña", nombre_cliente=None, es_caliente=False, necesita_traslado=False):
    try:
        h, m = map(int, hora.split(":"))
        hora_decimal = h + m / 60
        if not ((9 <= hora_decimal <= 11) or (14.5 <= hora_decimal <= 18)):
            return {"error": "Horario fuera de disponibilidad", "horarios": "9-11 AM o 2:30-6 PM"}
    except Exception:
        return {"error": f"Formato hora invalido: {hora}"}

    conn = get_db_connection()
    if not conn:
        return {"error": "BD no disponible"}
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO citas (usuario_id, fecha, hora, tipo, confirmado, es_caliente, nombre_cliente, telefono, necesita_traslado)
            VALUES (%s, %s, %s, %s, FALSE, %s, %s, %s, %s) RETURNING id
        """, (usuario_id, fecha, hora, f"visita_{desarrollo.lower().replace(' ','_')}", es_caliente, nombre_cliente,
              normalizar_telefono(usuario_id), necesita_traslado))
        cita_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        # Notificar a Salvador
        nombre_display = nombre_cliente or "Cliente"
        emoji = "🔥" if es_caliente else "✅"
        urgencia = " URGENTE" if es_caliente else ""
        msg = f"{emoji} CITA AGENDADA{urgencia}\n"
        msg += f"Desarrollo: {desarrollo}\n"
        msg += f"Cliente: {nombre_display}\n"
        msg += f"Tel: {normalizar_telefono(usuario_id)}\n"
        msg += f"Fecha: {fecha} a las {hora}\n"
        if necesita_traslado:
            msg += "🚗 Necesita traslado desde estacion Lomas del Sur\n"
        msg += f"ID cita: {cita_id}"
        encolar_notificacion("5213334969274", msg, tipo="cita_agendada")

        traslado_txt = ""
        if necesita_traslado:
            traslado_txt = " Salvador puede recogerte en la estacion Lomas del Sur (Linea 4). Avisame cuando vengas en camino. El va en un Nissan March blanco en la gasolinera frente a la estacion."

        return {
            "cita_id": cita_id,
            "fecha": fecha,
            "hora": hora,
            "es_caliente": es_caliente,
            "mensaje": f"Cita confirmada el {fecha} a las {hora}.{traslado_txt}",
            "siguiente_paso": "escalar_a_salvador"
        }
    except Exception as e:
        return {"error": str(e)}


def tool_guardar_lead_update(usuario_id, nombre=None, telefono=None, etapa=None,
                              monto_credito=None, tipo_credito=None, nivel_interes=None,
                              caliente=None, notas=None):
    conn = get_db_connection()
    if not conn:
        return {"error": "BD no disponible"}
    try:
        cur = conn.cursor()
        tel = telefono or normalizar_telefono(usuario_id)

        # Obtener etapa anterior para registro de historial
        cur.execute("SELECT etapa FROM leads WHERE usuario_id = %s", (usuario_id,))
        row = cur.fetchone()
        etapa_anterior = row[0] if row else None

        # Insert/update
        cur.execute("""
            INSERT INTO leads (usuario_id, nombre, telefono, etapa, monto_credito, tipo_credito,
                               nivel_interes, caliente, notas, timestamp, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), NOW())
            ON CONFLICT (usuario_id) DO UPDATE SET
                nombre = COALESCE(EXCLUDED.nombre, leads.nombre),
                telefono = COALESCE(EXCLUDED.telefono, leads.telefono),
                etapa = COALESCE(EXCLUDED.etapa, leads.etapa),
                monto_credito = COALESCE(EXCLUDED.monto_credito, leads.monto_credito),
                tipo_credito = COALESCE(EXCLUDED.tipo_credito, leads.tipo_credito),
                nivel_interes = COALESCE(EXCLUDED.nivel_interes, leads.nivel_interes),
                caliente = COALESCE(EXCLUDED.caliente, leads.caliente),
                notas = COALESCE(EXCLUDED.notas, leads.notas),
                updated_at = NOW()
        """, (usuario_id, nombre, tel, etapa, monto_credito, tipo_credito,
              nivel_interes, caliente, notas))

        # Registrar historial si cambio etapa
        if etapa and etapa != etapa_anterior:
            cur.execute("""
                INSERT INTO lead_etapas_historial (usuario_id, etapa_anterior, etapa_nueva, cambiado_por, notas)
                VALUES (%s, %s, %s, 'sandra', %s)
            """, (usuario_id, etapa_anterior, etapa, notas))

        conn.commit()
        cur.close()
        conn.close()
        return {"mensaje": "Lead actualizado"}
    except Exception as e:
        return {"error": str(e)}


def tool_escalar_a_salvador(usuario_id, motivo, resumen, urgente=False, conversation_id=None):
    """Escala a Salvador y pausa Sandra"""
    if conversation_id:
        pausar_sandra_para(conversation_id, usuario_id, f"Escalado: {motivo}")
    else:
        # Fallback: pausar por usuario_id usando valor dummy para conversation_id
        pausar_sandra_para(f"user_{usuario_id}", usuario_id, f"Escalado: {motivo}")

    emoji_map = {
        "lead_caliente": "🔥",
        "cita_agendada": "✅",
        "tema_complejo": "⚠️",
        "pidio_humano": "🙋"
    }
    emoji = emoji_map.get(motivo, "📩")
    urgencia = " URGENTE" if urgente else ""
    msg = f"{emoji} LEAD ESCALADO{urgencia}\n"
    msg += f"Tel: {normalizar_telefono(usuario_id)}\n"
    msg += f"Motivo: {motivo}\n"
    msg += f"Resumen: {resumen}"
    encolar_notificacion("5213334969274", msg, tipo=motivo)
    return {
        "escalado": True,
        "mensaje_para_cliente": "Perfecto, en un momento Salvador te atiende personalmente. Gracias.",
        "instruccion": "Sandra PAUSADA para esta conversacion."
    }


# =====================================================================
# IMPLEMENTACION TOOLS ASISTENTE
# =====================================================================
def tool_reporte_clientes_hoy():
    conn = get_db_connection()
    if not conn:
        return {"error": "BD no disponible"}
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT DISTINCT l.usuario_id, l.nombre, l.telefono, l.etapa, l.caliente
            FROM leads l
            WHERE DATE(l.created_at) = CURRENT_DATE
            ORDER BY l.created_at DESC
        """)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return {
            "total": len(rows),
            "clientes": rows,
            "fecha": str(date.today())
        }
    except Exception as e:
        return {"error": str(e)}


def tool_reporte_clientes_periodo(dias_atras):
    conn = get_db_connection()
    if not conn:
        return {"error": "BD no disponible"}
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT l.usuario_id, l.nombre, l.telefono, l.etapa, l.caliente, l.created_at
            FROM leads l
            WHERE l.created_at >= NOW() - INTERVAL '%s days'
            ORDER BY l.created_at DESC
        """, (dias_atras,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        # Stringify dates
        for r in rows:
            if "created_at" in r and r["created_at"]:
                r["created_at"] = str(r["created_at"])
        return {"total": len(rows), "clientes": rows, "dias": dias_atras}
    except Exception as e:
        return {"error": str(e)}


def tool_reporte_citas_agendadas(rango):
    conn = get_db_connection()
    if not conn:
        return {"error": "BD no disponible"}
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if rango == "hoy":
            cur.execute("SELECT * FROM citas WHERE DATE(fecha) = CURRENT_DATE ORDER BY hora")
        elif rango == "manana":
            cur.execute("SELECT * FROM citas WHERE DATE(fecha) = CURRENT_DATE + INTERVAL '1 day' ORDER BY hora")
        elif rango == "semana":
            cur.execute("SELECT * FROM citas WHERE DATE(fecha) BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '7 days' ORDER BY fecha, hora")
        else:
            cur.execute("SELECT * FROM citas WHERE DATE(fecha) >= CURRENT_DATE ORDER BY fecha, hora LIMIT 30")
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        for r in rows:
            for k in r:
                if hasattr(r[k], "isoformat"):
                    r[k] = r[k].isoformat()
        return {"total": len(rows), "citas": rows, "rango": rango}
    except Exception as e:
        return {"error": str(e)}


def tool_reporte_leads_calificados():
    conn = get_db_connection()
    if not conn:
        return {"error": "BD no disponible"}
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT usuario_id, nombre, telefono, etapa, monto_credito, nivel_interes, caliente, updated_at
            FROM leads
            WHERE etapa IN ('cita_agendada', 'acepto_propuesta_cita')
            ORDER BY caliente DESC, updated_at DESC
        """)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        for r in rows:
            if "updated_at" in r and r["updated_at"]:
                r["updated_at"] = str(r["updated_at"])
        return {"total": len(rows), "leads": rows}
    except Exception as e:
        return {"error": str(e)}


def tool_reporte_leads_calientes():
    conn = get_db_connection()
    if not conn:
        return {"error": "BD no disponible"}
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT usuario_id, nombre, telefono, etapa, monto_credito, notas, updated_at
            FROM leads
            WHERE caliente = TRUE AND etapa NOT IN ('venta_cerrada', 'cancelado', 'perdido')
            ORDER BY updated_at DESC
        """)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        for r in rows:
            if "updated_at" in r and r["updated_at"]:
                r["updated_at"] = str(r["updated_at"])
        return {"total": len(rows), "leads_calientes": rows}
    except Exception as e:
        return {"error": str(e)}


def tool_buscar_lead_por_nombre(nombre):
    conn = get_db_connection()
    if not conn:
        return {"error": "BD no disponible"}
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM leads
            WHERE LOWER(nombre) LIKE LOWER(%s)
            ORDER BY updated_at DESC
            LIMIT 10
        """, (f"%{nombre}%",))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        for r in rows:
            for k in r:
                if hasattr(r[k], "isoformat"):
                    r[k] = r[k].isoformat()
        return {"total": len(rows), "resultados": rows}
    except Exception as e:
        return {"error": str(e)}


def tool_resumen_dia():
    conn = get_db_connection()
    if not conn:
        return {"error": "BD no disponible"}
    try:
        cur = conn.cursor()
        # Clientes nuevos hoy
        cur.execute("SELECT COUNT(*) FROM leads WHERE DATE(created_at) = CURRENT_DATE")
        nuevos = cur.fetchone()[0]
        # Citas hoy
        cur.execute("SELECT COUNT(*) FROM citas WHERE DATE(fecha) = CURRENT_DATE")
        citas_hoy = cur.fetchone()[0]
        # Citas agendadas (futuras)
        cur.execute("SELECT COUNT(*) FROM citas WHERE DATE(fecha) > CURRENT_DATE")
        citas_pendientes = cur.fetchone()[0]
        # Leads calificados pendientes
        cur.execute("SELECT COUNT(*) FROM leads WHERE etapa = 'cita_agendada'")
        calificados = cur.fetchone()[0]
        # Leads calientes
        cur.execute("SELECT COUNT(*) FROM leads WHERE caliente = TRUE AND etapa NOT IN ('venta_cerrada', 'cancelado', 'perdido')")
        calientes = cur.fetchone()[0]
        cur.close()
        conn.close()
        return {
            "fecha": str(date.today()),
            "clientes_nuevos_hoy": nuevos,
            "citas_hoy": citas_hoy,
            "citas_pendientes_futuras": citas_pendientes,
            "leads_calificados": calificados,
            "leads_calientes": calientes
        }
    except Exception as e:
        return {"error": str(e)}


def tool_actualizar_etapa_lead(telefono_cliente, etapa_nueva, notas=None):
    tel = normalizar_telefono(telefono_cliente)
    conn = get_db_connection()
    if not conn:
        return {"error": "BD no disponible"}
    try:
        cur = conn.cursor()
        cur.execute("SELECT usuario_id, etapa FROM leads WHERE telefono = %s OR usuario_id = %s", (tel, tel))
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return {"error": f"No se encontro lead con telefono {tel}"}
        usuario_id, etapa_anterior = row
        cur.execute("""
            UPDATE leads SET etapa = %s, notas = COALESCE(%s, notas), updated_at = NOW()
            WHERE telefono = %s OR usuario_id = %s
        """, (etapa_nueva, notas, tel, tel))
        cur.execute("""
            INSERT INTO lead_etapas_historial (usuario_id, etapa_anterior, etapa_nueva, cambiado_por, notas)
            VALUES (%s, %s, %s, 'salvador', %s)
        """, (usuario_id, etapa_anterior, etapa_nueva, notas))
        conn.commit()
        cur.close()
        conn.close()
        return {"actualizado": True, "etapa_anterior": etapa_anterior, "etapa_nueva": etapa_nueva}
    except Exception as e:
        return {"error": str(e)}


def tool_pausar_sandra_cliente(telefono_cliente):
    tel = normalizar_telefono(telefono_cliente)
    # Buscar conversation_id del cliente
    conn = get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT conversation_id FROM sandra_control WHERE usuario_id = %s", (tel,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row:
                pausar_sandra_para(row[0], tel, "Comando de Salvador via asistente")
                return {"pausada": True, "telefono": tel}
        except Exception:
            pass
    # Fallback
    pausar_sandra_para(f"user_{tel}", tel, "Comando de Salvador via asistente")
    return {"pausada": True, "telefono": tel}


def tool_actualizar_precio_castana(nivel, precio_base, precio_con_escrituras=None):
    """Salvador actualiza precios de Castana sin tocar codigo ni BD manual"""
    nivel_map = {
        "planta_baja": "Planta baja",
        "primer_nivel": "Primer nivel",
        "segundo_nivel": "Segundo nivel",
        "planta baja": "Planta baja",
        "primer nivel": "Primer nivel",
        "segundo nivel": "Segundo nivel"
    }
    nivel_bd = nivel_map.get(nivel.lower(), nivel)

    if precio_con_escrituras is None:
        precio_con_escrituras = precio_base + 20000

    conn = get_db_connection()
    if not conn:
        return {"error": "BD no disponible"}
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE propiedades_castana
            SET precio_base = %s, precio_con_escrituras = %s
            WHERE nivel ILIKE %s
            RETURNING id, nivel
        """, (precio_base, precio_con_escrituras, nivel_bd))
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return {"error": f"No se encontro nivel: {nivel_bd}"}
        conn.commit()
        cur.close()
        conn.close()
        return {
            "actualizado": True,
            "nivel": row[1],
            "precio_base_nuevo": float(precio_base),
            "precio_con_escrituras_nuevo": float(precio_con_escrituras),
            "mensaje": f"Precio actualizado. {row[1]}: ${precio_base:,.0f} (sin escrituras) | ${precio_con_escrituras:,.0f} (con escrituras)"
        }
    except Exception as e:
        return {"error": str(e)}


def tool_ver_precios_castana():
    """Muestra precios actuales de todos los niveles"""
    conn = get_db_connection()
    if not conn:
        return {"error": "BD no disponible"}
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT nivel, precio_base, precio_con_escrituras
            FROM propiedades_castana
            WHERE activo = TRUE
            ORDER BY precio_base ASC
        """)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        for r in rows:
            r["precio_base"] = float(r["precio_base"])
            r["precio_con_escrituras"] = float(r["precio_con_escrituras"])
        return {"precios_actuales": rows}
    except Exception as e:
        return {"error": str(e)}


# =====================================================================
# DISPATCHER
# =====================================================================
def procesar_herramienta(nombre, params, contexto_usuario=None, contexto_conversation_id=None):
    try:
        # Tools agente
        if nombre == "consultar_inventario":
            return tool_consultar_inventario(
                desarrollo=params.get("desarrollo"),
                presupuesto_max=params.get("presupuesto_max"),
                zona=params.get("zona"),
                tipo=params.get("tipo")
            )
        elif nombre == "evaluar_credito":
            return tool_evaluar_credito(
                monto_credito=params.get("monto_credito"),
                tipo_credito=params.get("tipo_credito"),
                zona_preferencia=params.get("zona_preferencia")
            )
        elif nombre == "agendar_visita":
            return tool_agendar_visita(
                usuario_id=params.get("usuario_id") or contexto_usuario,
                fecha=params.get("fecha"),
                hora=params.get("hora"),
                desarrollo=params.get("desarrollo", "Castaña"),
                nombre_cliente=params.get("nombre_cliente"),
                es_caliente=params.get("es_caliente", False),
                necesita_traslado=params.get("necesita_traslado", False)
            )
        elif nombre == "guardar_lead_update":
            return tool_guardar_lead_update(
                usuario_id=params.get("usuario_id") or contexto_usuario,
                nombre=params.get("nombre"),
                telefono=params.get("telefono"),
                etapa=params.get("etapa"),
                monto_credito=params.get("monto_credito"),
                tipo_credito=params.get("tipo_credito"),
                nivel_interes=params.get("nivel_interes"),
                caliente=params.get("caliente"),
                notas=params.get("notas")
            )
        elif nombre == "escalar_a_salvador":
            return tool_escalar_a_salvador(
                usuario_id=params.get("usuario_id") or contexto_usuario,
                motivo=params.get("motivo"),
                resumen=params.get("resumen"),
                urgente=params.get("urgente", False),
                conversation_id=contexto_conversation_id
            )

        # Tools asistente
        elif nombre == "reporte_clientes_hoy":
            return tool_reporte_clientes_hoy()
        elif nombre == "reporte_clientes_periodo":
            return tool_reporte_clientes_periodo(params.get("dias_atras", 7))
        elif nombre == "reporte_citas_agendadas":
            return tool_reporte_citas_agendadas(params.get("rango", "hoy"))
        elif nombre == "reporte_leads_calificados":
            return tool_reporte_leads_calificados()
        elif nombre == "reporte_leads_calientes":
            return tool_reporte_leads_calientes()
        elif nombre == "buscar_lead_por_nombre":
            return tool_buscar_lead_por_nombre(params.get("nombre", ""))
        elif nombre == "resumen_dia":
            return tool_resumen_dia()
        elif nombre == "actualizar_etapa_lead":
            return tool_actualizar_etapa_lead(
                telefono_cliente=params.get("telefono_cliente"),
                etapa_nueva=params.get("etapa_nueva"),
                notas=params.get("notas")
            )
        elif nombre == "pausar_sandra_cliente":
            return tool_pausar_sandra_cliente(params.get("telefono_cliente"))
        elif nombre == "actualizar_precio_castana":
            return tool_actualizar_precio_castana(
                nivel=params.get("nivel"),
                precio_base=params.get("precio_base"),
                precio_con_escrituras=params.get("precio_con_escrituras")
            )
        elif nombre == "ver_precios_castana":
            return tool_ver_precios_castana()

        else:
            return {"error": f"Herramienta desconocida: {nombre}"}
    except Exception as e:
        return {"error": f"Error en {nombre}: {str(e)}"}


# =====================================================================
# SYSTEM PROMPTS
# =====================================================================
SYSTEM_PROMPT_AGENTE = """Eres Sandra, asistente virtual de Salvador Navarro Bienes Raices. Experta en el inventario completo: Fraccionamiento CASTAÑA (Tlajomulco) es tu producto estrella, pero tambien manejas Rinconada San Alejandro y todo el inventario de Salvador.

## PERSONALIDAD
- Profesional, amable, cercana, natural como mexicana de Guadalajara.
- Mensajes CORTOS estilo WhatsApp (1-3 lineas).
- Si te preguntan si eres IA, di: "Soy Sandra, asistente virtual de Salvador Navarro Bienes Raices
- Emojis moderados: 👍 😊 🏠 👌🏼

## OBJETIVO PRINCIPAL
Llevar al cliente desde primer contacto hasta AGENDAR VISITA. Tu producto estrella es Castaña, pero NUNCA dejes ir un cliente sin opciones.

## ESTRATEGIA DE VENTAS
1. Siempre empieza ofreciendo Castaña (producto estrella)
2. Si NO califica o NO le gusta la zona → busca alternativas con consultar_inventario
3. Si NO hay alternativas en BD → comparte link de inventario general y escala a Salvador
4. NUNCA digas "no tenemos nada para ti" sin antes buscar alternativas

## PRECIOS (CRITICO)
NUNCA cotices un precio de memoria. SIEMPRE consulta con consultar_inventario o evaluar_credito primero.
Las escrituras son $20,000 (no negociables, las cobra el notario).

## HORARIOS DE VISITA
- Lunes a Domingo
- 9:00-11:00 AM o 2:30-6:00 PM (despues oscurece)

## DETECCION DE LEAD CALIENTE 🔥
Si el cliente usa palabras como "hoy", "mañana", "ya", "ahorita", "ahora", "voy en camino":
1. NO empujes la cita a fin de semana
2. Agenda para ESE momento
3. Marca caliente=true en guardar_lead_update
4. Pasa es_caliente=true a agendar_visita
5. Escala a Salvador con urgente=true

## FLUJO IDEAL

### Paso 1: Primer contacto
1. Saludo corto
2. "Te atiende Sandra, asistente virtual de Salvador Navarro Bienes Raices"
3. consultar_inventario (sin parametros = Castaña destacada)
4. Presenta ubicacion + descripcion + precios
5. "¿Tu credito es Infonavit?"

### Paso 2: Calificacion
- "¿Ya sabes el monto de tu credito?"
- Cuando dice monto → evaluar_credito
- guardar_lead_update con monto_credito y tipo_credito

### Paso 3: Segun resultado
- SI CALIFICA para algo → confirmar opcion y proponer visita
- NO ALCANZA para nada → consultar_inventario con presupuesto_max para buscar alternativas mas baratas
- NO LE GUSTA LA ZONA → consultar_inventario con zona diferente
- PIDE CASA USADA → consultar_inventario con tipo="casa_usada"
- SIN OPCIONES EN BD → comparte link inventario general, escala a Salvador

### Paso 4: Agendar
- "¿Que dia te queda mejor?"
- "¿Vienes en transporte propio o necesitas que te recojan?"
- agendar_visita (con el desarrollo correcto)
- escalar_a_salvador con motivo "cita_agendada"

### Paso 5: Escalar
Escala a Salvador inmediatamente cuando:
- Pide ubicacion exacta del depa
- Quiere unir creditos (Cofinavit)
- Pide tramites Infonavit (contrasena, NSS)
- Pregunta por propiedades que no estan en BD
- Pide hablar con humano

## REGLAS CRITICAS
1. NUNCA inventes datos. NUNCA memorices precios. Siempre consulta la BD.
2. NUNCA te presentes de nuevo si ya hay historial.
3. NUNCA repitas preguntas ya respondidas.
4. NUNCA pidas contrasenas, NSS, RFC. Escala a Salvador.
5. NUNCA respondas a colegas/jefes. Si hablan de "guardia", "expediente", "junta" → escalar_a_salvador.

## FRASES DE SALVADOR (usalas)
"Te atiende Sandra, asistente virtual de Salvador Navarro Bienes Raices"
"Excelente!" "Perfecto 👌" "Muy bien" "A muy bien!"
"Quedo al pendiente" "Si a tus ordenes" "Te alcanza perfecto"

NO uses: "¿En qué puedo ayudarte hoy?", "¿Buscas casa, departamento o terreno?", "¡Excelente! Me encanta tu interés"
"""


SYSTEM_PROMPT_ASISTENTE = """Eres Sandra, asistente personal de Salvador Navarro. El que te esta escribiendo es SALVADOR (tu jefe), no un cliente.

## TU ROL EN ESTE MODO
Eres su brazo derecho administrativo. Tienes acceso completo a la base de datos de clientes, citas y leads. Le das reportes, le buscas informacion, le actualizas estados, y le notificas pendientes.

## TONO
- Profesional pero cercana, como una asistente real
- Mensajes claros y directos
- Cuando das reportes, usa formato legible (listas, totales)
- Puedes hacer recomendaciones si detectas patrones

## QUE PUEDES HACER

### Reportes (sin que te pida detalle):
- "¿cuantos clientes hoy?" -> reporte_clientes_hoy
- "¿como va la semana?" -> reporte_clientes_periodo con dias_atras=7
- "dame el resumen" -> resumen_dia
- "¿que citas tengo?" -> reporte_citas_agendadas con rango="hoy" o segun pida
- "¿quien esta calificado?" -> reporte_leads_calificados
- "¿hay leads calientes?" -> reporte_leads_calientes

### Busquedas:
- "buscame a Lourdes" -> buscar_lead_por_nombre
- "como va el cliente Martin" -> buscar_lead_por_nombre

### Acciones:
- "el cliente X ya firmo" -> actualizar_etapa_lead con etapa_nueva="firmo_solicitud"
- "ya entregue el expediente de Y" -> actualizar_etapa_lead etapa_nueva="entregado_a_david"
- "pausa a Sandra con el cliente Z" -> pausar_sandra_cliente

### Gestion de precios e inventario:
- "¿cuanto cuesta el segundo nivel?" / "dame los precios" -> ver_precios_castana
- "el segundo nivel sube a 690000" -> actualizar_precio_castana
- "actualiza primer nivel a 700000 con escrituras 720000" -> actualizar_precio_castana
- Si Salvador no especifica precio_con_escrituras, automaticamente se suma $20,000 al precio_base
- Para agregar nuevos desarrollos al inventario, Salvador debe pedir ayuda tecnica (se agregan directo en BD)

## ETAPAS DEL EMBUDO (17)
Sandra automatiza 1-4. Salvador maneja 5-17.

1. nuevo_contacto
2. pidio_info
3. acepto_propuesta_cita
4. cita_agendada (Sandra termina aqui)
5. visita_realizada
6. firmo_solicitud
7. expediente_completo
8. entregado_a_david
9. ingresado_infonavit
10. ubicacion_asignada
11. aviso_retencion_emitido
12. patron_acepto_acuse
13. acuse_entregado
14. cita_notario_agendada
15. escritura_firmada
16. entrega_llaves
17. venta_cerrada
99. cancelado / no_califica / perdido

## REGLAS
- Salvador es tu jefe. Le hablas con respeto pero con confianza.
- No le pidas explicaciones de mas. Si dice "el cliente X ya firmo", ejecuta.
- Si te pide algo que no puedes hacer aun (etapas 5-17 sin CRM), dile honestamente: "Salvador, eso se ejecuta cuando este el CRM. Por ahora puedo registrarlo manualmente con actualizar_etapa_lead si me das el telefono del cliente."
- Salvador conoce el negocio. No le expliques cosas obvias.

## SALUDO INICIAL
Si Salvador te saluda ("hola Sandra", "buenos dias"), saluda cordialmente y di que cuentas con: reportes del dia, citas, leads calificados, busquedas por nombre. Ofrece una accion concreta.

NO digas "Soy Sandra, asistente virtual de Salvador Navarro Bienes Raices" -> eso es para clientes. A Salvador le dices: "Hola Salvador, ¿en que te ayudo?"
"""


# =====================================================================
# ENDPOINT PRINCIPAL /chat
# =====================================================================
@app.route("/chat", methods=["POST"])
def chat():
    data = request.json or {}
    usuario_id = data.get("usuario_id")
    mensaje_usuario = data.get("mensaje")
    historial_previo = data.get("historial", [])
    conversation_id = data.get("conversation_id")

    if not usuario_id or not mensaje_usuario:
        return jsonify({"error": "Falta usuario_id o mensaje"}), 400

    # ============================================================
    # FILTRO 0: ¿Es comando "adelante Sandra"? -> REANUDAR
    # ============================================================
    if es_comando_reanudar(mensaje_usuario):
        if conversation_id:
            reanudar_sandra_para(conversation_id)
            print(f"[REANUDA] Comando 'adelante Sandra' en conv={conversation_id}")
        return jsonify({
            "respuesta": "",
            "ignorar": True,
            "motivo_ignorar": "comando_reanudar_sandra"
        })

    # ============================================================
    # FILTRO 1: ¿Es admin? -> MODO ASISTENTE
    # ============================================================
    admin_info = es_admin(usuario_id)
    if admin_info:
        admin_nombre = admin_info[0]
        return chat_modo_asistente(usuario_id, mensaje_usuario, historial_previo, admin_nombre)

    # ============================================================
    # FILTRO 2: ¿Blacklist? -> NO RESPONDER
    # ============================================================
    if esta_en_blacklist(usuario_id):
        return jsonify({
            "respuesta": "",
            "ignorar": True,
            "motivo_ignorar": "contacto_en_blacklist"
        })

    # ============================================================
    # FILTRO 3: ¿Pausada por conversacion? -> NO RESPONDER
    # ============================================================
    if not sandra_esta_activa(conversation_id, usuario_id):
        return jsonify({
            "respuesta": "",
            "sandra_pausada": True,
            "ignorar": True,
            "motivo_ignorar": "sandra_pausada"
        })

    # ============================================================
    # FILTRO 4: ¿Primer mensaje sin patron de campaña? -> NO RESPONDER
    # (Sin notificacion spam - solo ignora silenciosamente)
    # ============================================================
    if not historial_previo:
        historial_db = get_historial_conversacion(usuario_id, limite=2)
        if not historial_db:
            # Es PRIMER mensaje
            patrones_campana = [
                "deptos desde", "fraccionamiento castaña", "fraccionamiento castana",
                "fb.me/", "instagram.com/p/", "facebook.com/share",
                "👉 quiero", "👉 que precio", "👉 quiero agendar",
                "quiero agendar una visita", "quiero mas informacion",
                "quiero más información", "no me alcanza",
                "que precio manejan", "qué precio manejan",
                "información sobre", "informacion sobre", "info castaña",
                "interesa el depa", "depas desde", "678,000", "depas de"
            ]
            msg_lower = mensaje_usuario.lower()
            tiene_patron = any(p in msg_lower for p in patrones_campana)
            if not tiene_patron:
                # Primer mensaje sin patron de campaña: NO responder, NO notificar
                return jsonify({
                    "respuesta": "",
                    "ignorar": True,
                    "motivo_ignorar": "primer_mensaje_sin_patron_campana"
                })

    # ============================================================
    # MODO AGENTE: cliente potencial
    # ============================================================
    return chat_modo_agente(usuario_id, mensaje_usuario, historial_previo, conversation_id)


def chat_modo_asistente(usuario_id, mensaje_usuario, historial_previo, admin_nombre):
    """Salvador esta hablando con Sandra"""
    if not historial_previo:
        historial_previo = get_historial_conversacion(usuario_id, limite=20)

    messages = []
    for msg in historial_previo:
        rol = msg.get("rol") or msg.get("role")
        contenido = msg.get("mensaje") or msg.get("content") or msg.get("contenido")
        if rol and contenido:
            if rol in ("user", "cliente", "admin", "salvador"):
                rol = "user"
            elif rol in ("assistant", "sandra", "bot"):
                rol = "assistant"
            messages.append({"role": rol, "content": str(contenido)})

    messages.append({"role": "user", "content": mensaje_usuario})

    respuesta_final = None
    max_iter = 6
    iteracion = 0

    while iteracion < max_iter:
        iteracion += 1
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2048,
                system=SYSTEM_PROMPT_ASISTENTE.replace("Salvador", admin_nombre),
                tools=TOOLS_ASISTENTE,
                messages=messages
            )

            if response.stop_reason == "tool_use":
                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        respuesta_final = block.text
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        resultado = procesar_herramienta(block.name, block.input or {}, contexto_usuario=usuario_id)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(resultado, ensure_ascii=False, default=str)
                        })
                messages.append({"role": "user", "content": tool_results})
                continue

            elif response.stop_reason == "end_turn":
                textos = [b.text for b in response.content if b.type == "text"]
                respuesta_final = "\n".join(textos).strip()
                break
            else:
                textos = [b.text for b in response.content if b.type == "text"]
                respuesta_final = "\n".join(textos).strip() or "Un momento."
                break
        except Exception as e:
            print(f"[ASISTENTE] Error: {e}")
            respuesta_final = "Salvador, tuve un problema. Intenta de nuevo."
            break

    return jsonify({
        "respuesta": respuesta_final or "¿En que te ayudo?",
        "modo": "asistente",
        "lead_calificado": False,
        "datos_lead": {},
        "sandra_pausada": False,
        "ignorar": False
    })


def chat_modo_agente(usuario_id, mensaje_usuario, historial_previo, conversation_id=None):
    """Cliente potencial esta hablando con Sandra"""
    if not historial_previo:
        historial_previo = get_historial_conversacion(usuario_id, limite=20)

    # ============================================================
    # DISPARADOR KIT DE FOTOS CASTAÑA
    # ============================================================
    PALABRAS_FOTOS = [
        "informacion", "información", "info", "fotos", "foto",
        "castaña", "castana", "imagenes", "imágenes", "ver el depa",
        "como es", "cómo es", "interesa", "mas info", "más info"
    ]
    msg_lower_fotos = mensaje_usuario.lower()
    pidio_fotos = any(p in msg_lower_fotos for p in PALABRAS_FOTOS)

    # Anti-spam: solo mandar el kit si NO se ha mandado antes en esta conversacion
    ya_envie_kit = any(
        "[KIT_FOTOS_ENVIADO]" in str(
            m.get("mensaje") or m.get("content") or m.get("contenido") or ""
        )
        for m in historial_previo
    )

    if pidio_fotos and not ya_envie_kit:
        try:
            enviar_kit_castana(usuario_id)
            # Marcar en BD que ya se envio el kit (queda en el historial)
            registrar_kit_enviado(usuario_id)
        except Exception as e:
            print(f"[KIT] Error en disparador: {e}")

    # Detectar lead caliente desde el mensaje
    es_caliente_mensaje = detectar_palabra_caliente(mensaje_usuario)

    messages = []
    for msg in historial_previo:
        rol = msg.get("rol") or msg.get("role")
        contenido = msg.get("mensaje") or msg.get("content") or msg.get("contenido")
        if rol and contenido:
            if rol in ("user", "cliente"):
                rol = "user"
            elif rol in ("assistant", "sandra", "bot"):
                rol = "assistant"
            messages.append({"role": rol, "content": str(contenido)})

    # Si detectamos palabra caliente, pegarle una nota al system
    mensaje_con_alerta = mensaje_usuario
    if es_caliente_mensaje:
        mensaje_con_alerta = f"{mensaje_usuario}\n\n[NOTA INTERNA SISTEMA: El cliente uso palabras de URGENCIA INMEDIATA. Este es LEAD CALIENTE 🔥. NO empujes la cita a fin de semana, cierra hoy o mañana segun pidio. Marca caliente=true y escala a Salvador como urgente.]"

    messages.append({"role": "user", "content": mensaje_con_alerta})

    respuesta_final = None
    lead_calificado = False
    datos_lead_final = {}
    sandra_pausada = False
    max_iter = 6
    iteracion = 0

    while iteracion < max_iter:
        iteracion += 1
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=SYSTEM_PROMPT_AGENTE,
                tools=TOOLS_AGENTE,
                messages=messages
            )

            if response.stop_reason == "tool_use":
                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        respuesta_final = block.text
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_input = block.input or {}
                        if not tool_input.get("usuario_id") and block.name in (
                            "agendar_visita", "guardar_lead_update", "escalar_a_salvador"
                        ):
                            tool_input["usuario_id"] = usuario_id

                        resultado = procesar_herramienta(
                            block.name, tool_input,
                            contexto_usuario=usuario_id,
                            contexto_conversation_id=conversation_id
                        )

                        if block.name == "escalar_a_salvador":
                            sandra_pausada = True
                        if block.name == "guardar_lead_update" and tool_input.get("etapa") == "cita_agendada":
                            lead_calificado = True
                            datos_lead_final = {
                                "usuario_id": usuario_id,
                                "nombre": tool_input.get("nombre"),
                                "etapa": "cita_agendada",
                                "monto_credito": tool_input.get("monto_credito"),
                                "caliente": tool_input.get("caliente", False)
                            }

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(resultado, ensure_ascii=False, default=str)
                        })
                messages.append({"role": "user", "content": tool_results})
                continue

            elif response.stop_reason == "end_turn":
                textos = [b.text for b in response.content if b.type == "text"]
                respuesta_final = "\n".join(textos).strip()
                break
            else:
                textos = [b.text for b in response.content if b.type == "text"]
                respuesta_final = "\n".join(textos).strip() or "Dame un momento."
                break
        except Exception as e:
            print(f"[AGENTE] Error: {e}")
            respuesta_final = "Disculpa, dame un momento mientras reviso tu informacion."
            break

    return jsonify({
        "respuesta": respuesta_final or "Disculpa, dame un momento.",
        "modo": "agente",
        "lead_calificado": lead_calificado,
        "datos_lead": datos_lead_final,
        "sandra_pausada": sandra_pausada,
        "es_caliente": es_caliente_mensaje,
        "ignorar": False
    })


# =====================================================================
# ENDPOINTS AUXILIARES
# =====================================================================
@app.route("/manual-reply", methods=["POST"])
def manual_reply():
    """n8n llama esto cuando Salvador respondio manualmente a un cliente.
    Recibe conversation_id de Chatwoot."""
    data = request.json or {}
    conversation_id = data.get("conversation_id")
    usuario_id = data.get("usuario_id")

    if not conversation_id:
        return jsonify({"error": "Falta conversation_id"}), 400

    pausar_sandra_para(conversation_id, usuario_id, "Salvador respondio manualmente")
    return jsonify({"sandra_pausada": True, "conversation_id": conversation_id})


@app.route("/pausar-manual", methods=["POST"])
def pausar_manual():
    """Endpoint para el workflow de pausa manual.
    Recibe conversation_id y opcionalmente usuario_id."""
    data = request.json or {}
    conversation_id = data.get("conversation_id")
    usuario_id = data.get("usuario_id")

    if not conversation_id:
        return jsonify({"error": "Falta conversation_id"}), 400

    pausar_sandra_para(conversation_id, usuario_id, "Salvador intervino en la conversacion")
    return jsonify({"sandra_pausada": True, "conversation_id": conversation_id})


@app.route("/reanudar-sandra", methods=["POST"])
def reanudar_sandra():
    """Endpoint para reanudar Sandra en una conversacion."""
    data = request.json or {}
    conversation_id = data.get("conversation_id")

    if not conversation_id:
        return jsonify({"error": "Falta conversation_id"}), 400

    reanudar_sandra_para(conversation_id)
    return jsonify({"sandra_reanudada": True, "conversation_id": conversation_id})


@app.route("/notificaciones-pendientes", methods=["GET"])
def get_notificaciones():
    """n8n consulta este endpoint para enviar notificaciones pendientes a Salvador"""
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "BD no disponible"}), 500
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, destinatario, mensaje, tipo, created_at
            FROM notificaciones_pendientes
            WHERE enviado = FALSE
            ORDER BY created_at ASC
            LIMIT 50
        """)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        for r in rows:
            if "created_at" in r:
                r["created_at"] = str(r["created_at"])
        return jsonify({"total": len(rows), "notificaciones": rows})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/notificacion-enviada", methods=["POST"])
def marcar_notif_enviada():
    """n8n marca una notificacion como enviada despues de mandarla por WhatsApp"""
    data = request.json or {}
    notif_id = data.get("id")
    if not notif_id:
        return jsonify({"error": "Falta id"}), 400
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "BD no disponible"}), 500
    try:
        cur = conn.cursor()
        cur.execute("UPDATE notificaciones_pendientes SET enviado = TRUE, enviado_at = NOW() WHERE id = %s", (notif_id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/resumen-diario", methods=["POST"])
def trigger_resumen_diario():
    """n8n cron lo llama 8am y 8pm para que Sandra mande resumen a Salvador.
    Incluye nombres y telefonos de cada lead/cita."""
    data = request.json or {}
    momento = data.get("momento", "manana")  # manana | tarde

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "BD no disponible"}), 500

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Clientes nuevos hoy
        cur.execute("""
            SELECT nombre, telefono, etapa, caliente
            FROM leads WHERE DATE(created_at) = CURRENT_DATE
            ORDER BY created_at DESC
        """)
        clientes_nuevos = [dict(r) for r in cur.fetchall()]

        # Citas hoy
        cur.execute("""
            SELECT nombre_cliente, telefono, hora, tipo, es_caliente
            FROM citas WHERE DATE(fecha) = CURRENT_DATE
            ORDER BY hora
        """)
        citas_hoy = [dict(r) for r in cur.fetchall()]

        # Citas mañana
        cur.execute("""
            SELECT nombre_cliente, telefono, hora, tipo, es_caliente
            FROM citas WHERE DATE(fecha) = CURRENT_DATE + INTERVAL '1 day'
            ORDER BY hora
        """)
        citas_manana = [dict(r) for r in cur.fetchall()]

        # Citas futuras (proximos 7 dias, excluyendo hoy)
        cur.execute("""
            SELECT nombre_cliente, telefono, fecha, hora, tipo
            FROM citas WHERE DATE(fecha) > CURRENT_DATE AND DATE(fecha) <= CURRENT_DATE + INTERVAL '7 days'
            ORDER BY fecha, hora
        """)
        citas_futuras = [dict(r) for r in cur.fetchall()]

        # Leads calientes activos
        cur.execute("""
            SELECT nombre, telefono, etapa, notas
            FROM leads
            WHERE caliente = TRUE AND etapa NOT IN ('venta_cerrada', 'cancelado', 'perdido')
            ORDER BY updated_at DESC
        """)
        leads_calientes = [dict(r) for r in cur.fetchall()]

        # Leads calificados pendientes
        cur.execute("""
            SELECT nombre, telefono, etapa, monto_credito
            FROM leads
            WHERE etapa IN ('cita_agendada', 'acepto_propuesta_cita')
            ORDER BY caliente DESC, updated_at DESC
        """)
        leads_calificados = [dict(r) for r in cur.fetchall()]

        cur.close()
        conn.close()

        fecha_hoy = str(date.today())

        if momento == "manana":
            msg = f"☀️ Buenos dias Salvador\n\n"
            msg += f"📋 Resumen ({fecha_hoy})\n\n"

            # Citas hoy
            msg += f"📅 Citas hoy ({len(citas_hoy)}):\n"
            if citas_hoy:
                for c in citas_hoy:
                    nombre = c.get("nombre_cliente") or "Sin nombre"
                    tel = c.get("telefono") or "?"
                    hora = str(c.get("hora", ""))[:5]
                    caliente = " 🔥" if c.get("es_caliente") else ""
                    msg += f"• {nombre} - {tel} - {hora}{caliente}\n"
            else:
                msg += "• Sin citas\n"

            # Citas mañana
            if citas_manana:
                msg += f"\n📅 Citas mañana ({len(citas_manana)}):\n"
                for c in citas_manana:
                    nombre = c.get("nombre_cliente") or "Sin nombre"
                    tel = c.get("telefono") or "?"
                    hora = str(c.get("hora", ""))[:5]
                    msg += f"• {nombre} - {tel} - {hora}\n"

            # Leads calientes
            msg += f"\n🔥 Leads calientes ({len(leads_calientes)}):\n"
            if leads_calientes:
                for l in leads_calientes:
                    nombre = l.get("nombre") or "Sin nombre"
                    tel = l.get("telefono") or "?"
                    msg += f"• {nombre} - {tel}\n"
            else:
                msg += "• Ninguno\n"

            # Leads calificados
            msg += f"\n⏳ Calificados pendientes ({len(leads_calificados)}):\n"
            if leads_calificados:
                for l in leads_calificados:
                    nombre = l.get("nombre") or "Sin nombre"
                    tel = l.get("telefono") or "?"
                    monto = l.get("monto_credito")
                    monto_txt = f" - ${float(monto):,.0f}" if monto else ""
                    msg += f"• {nombre} - {tel}{monto_txt}\n"
            else:
                msg += "• Ninguno\n"

        else:  # tarde (8pm)
            msg = f"🌙 Resumen del dia ({fecha_hoy})\n\n"

            # Clientes nuevos
            msg += f"👤 Clientes nuevos ({len(clientes_nuevos)}):\n"
            if clientes_nuevos:
                for c in clientes_nuevos:
                    nombre = c.get("nombre") or "Sin nombre"
                    tel = c.get("telefono") or "?"
                    caliente = " 🔥" if c.get("caliente") else ""
                    msg += f"• {nombre} - {tel}{caliente}\n"
            else:
                msg += "• Ninguno hoy\n"

            # Citas atendidas hoy
            msg += f"\n📅 Citas hoy ({len(citas_hoy)}):\n"
            if citas_hoy:
                for c in citas_hoy:
                    nombre = c.get("nombre_cliente") or "Sin nombre"
                    tel = c.get("telefono") or "?"
                    hora = str(c.get("hora", ""))[:5]
                    msg += f"• {nombre} - {tel} - {hora}\n"
            else:
                msg += "• Sin citas hoy\n"

            # Leads calientes
            msg += f"\n🔥 Leads calientes ({len(leads_calientes)}):\n"
            if leads_calientes:
                for l in leads_calientes:
                    nombre = l.get("nombre") or "Sin nombre"
                    tel = l.get("telefono") or "?"
                    notas = l.get("notas")
                    notas_txt = f" - {notas[:50]}" if notas else ""
                    msg += f"• {nombre} - {tel}{notas_txt}\n"
            else:
                msg += "• Ninguno\n"

            # Leads calificados
            msg += f"\n⏳ Calificados pendientes ({len(leads_calificados)}):\n"
            if leads_calificados:
                for l in leads_calificados:
                    nombre = l.get("nombre") or "Sin nombre"
                    tel = l.get("telefono") or "?"
                    etapa = l.get("etapa", "")
                    msg += f"• {nombre} - {tel} - {etapa}\n"
            else:
                msg += "• Ninguno\n"

            msg += "\nDescansa! 👋"

        encolar_notificacion("5213334969274", msg, tipo=f"resumen_{momento}")

        resumen_data = {
            "fecha": fecha_hoy,
            "clientes_nuevos_hoy": len(clientes_nuevos),
            "citas_hoy": len(citas_hoy),
            "citas_manana": len(citas_manana),
            "leads_calientes": len(leads_calientes),
            "leads_calificados": len(leads_calificados)
        }
        return jsonify({"ok": True, "resumen": resumen_data})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    db_ok = False
    conn = get_db_connection()
    if conn:
        db_ok = True
        conn.close()
    return jsonify({
        "status": "ok",
        "agent": "Sandra V2.2",
        "version": "2.2.0",
        "modos": ["agente", "asistente"],
        "database": "connected" if db_ok else "disconnected",
        "tools_agente": len(TOOLS_AGENTE),
        "tools_asistente": len(TOOLS_ASISTENTE)
    })


# =====================================================================
# MAIN
# =====================================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)

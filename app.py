from flask import Flask, request, jsonify
from anthropic import Anthropic
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import os
from datetime import datetime

app = Flask(__name__)

# Configuración
DATABASE = os.getenv("DATABASE_URL", "postgresql://usuario_crm:password@proyecto_piloto_agente_inmobiliario:5432/CRM")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Inicializar cliente Anthropic
client = Anthropic()

# Herramientas disponibles para Claude
TOOLS = [
    {
        "name": "buscar_propiedades",
        "description": "Busca propiedades disponibles por desarrollo, precio máximo y número de recámaras",
        "input_schema": {
            "type": "object",
            "properties": {
                "desarrollo": {
                    "type": "string",
                    "description": "Nombre del desarrollo (ej: Castaña, Rinconada San Alejandro)"
                },
                "precio_max": {
                    "type": "number",
                    "description": "Precio máximo en pesos"
                },
                "recamaras": {
                    "type": "integer",
                    "description": "Número de recámaras"
                }
            },
            "required": []
        }
    },
    {
        "name": "calcular_infonavit",
        "description": "Calcula cuánto crédito Infonavit puede solicitar una persona",
        "input_schema": {
            "type": "object",
            "properties": {
                "salario": {
                    "type": "number",
                    "description": "Salario mensual en pesos"
                },
                "antigüedad": {
                    "type": "integer",
                    "description": "Años de antigüedad en el trabajo"
                }
            },
            "required": ["salario"]
        }
    },
    {
        "name": "agendar_cita",
        "description": "Agenda una cita para visita a propiedad",
        "input_schema": {
            "type": "object",
            "properties": {
                "usuario_id": {
                    "type": "string",
                    "description": "ID del cliente"
                },
                "fecha": {
                    "type": "string",
                    "description": "Fecha de la cita (YYYY-MM-DD)"
                },
                "hora": {
                    "type": "string",
                    "description": "Hora de la cita (HH:MM)"
                },
                "tipo": {
                    "type": "string",
                    "description": "Tipo de cita (visita, consulta, etc)"
                }
            },
            "required": ["usuario_id", "fecha", "hora"]
        }
    },
    {
        "name": "guardar_lead_update",
        "description": "Guarda o actualiza información del lead",
        "input_schema": {
            "type": "object",
            "properties": {
                "usuario_id": {
                    "type": "string",
                    "description": "ID único del cliente"
                },
                "etapa": {
                    "type": "string",
                    "description": "Etapa del lead (prospecto, calificado, en_negociacion, etc)"
                },
                "interes": {
                    "type": "string",
                    "description": "Propiedad o desarrollo de interés"
                },
                "notas": {
                    "type": "string",
                    "description": "Notas adicionales"
                }
            },
            "required": ["usuario_id"]
        }
    },
    {
        "name": "get_lead_context",
        "description": "Obtiene el contexto/historial de un lead desde la BD",
        "input_schema": {
            "type": "object",
            "properties": {
                "usuario_id": {
                    "type": "string",
                    "description": "ID del cliente"
                }
            },
            "required": ["usuario_id"]
        }
    }
]

# ========== FUNCIONES DE BD ==========

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE)
        return conn
    except Exception as e:
        print(f"Error conectando a BD: {e}")
        return None

def buscar_propiedades_bd(desarrollo=None, precio_max=None, recamaras=None):
    conn = get_db_connection()
    if not conn:
        return {"error": "No se pudo conectar a la BD"}
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        query = "SELECT id, nombre, desarrollo, precio, recamaras, m2, descripcion FROM propiedades WHERE 1=1"
        params = []
        
        if desarrollo:
            query += " AND desarrollo ILIKE %s"
            params.append(f"%{desarrollo}%")
        if precio_max:
            query += " AND precio <= %s"
            params.append(precio_max)
        if recamaras:
            query += " AND recamaras >= %s"
            params.append(recamaras)
        
        query += " LIMIT 10"
        cur.execute(query, params)
        propiedades = cur.fetchall()
        cur.close()
        conn.close()
        return {"propiedades": [dict(p) for p in propiedades]}
    except Exception as e:
        return {"error": str(e)}

def calcular_infonavit_bd(salario, antigüedad=0):
    # Fórmula simplificada Infonavit
    factor_base = 4
    factor_antigüedad = min(antigüedad / 10, 0.5)  # Máximo 50% extra
    multiplicador = factor_base * (1 + factor_antigüedad)
    credito_estimado = salario * multiplicador * 120  # 120 meses aprox
    return {"credito_estimado": round(credito_estimado, 0)}

def agendar_cita_bd(usuario_id, fecha, hora, tipo="visita"):
    conn = get_db_connection()
    if not conn:
        return {"error": "No se pudo conectar a la BD"}
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO citas (usuario_id, fecha, hora, tipo, confirmado)
            VALUES (%s, %s, %s, %s, FALSE)
            RETURNING id
        """, (usuario_id, fecha, hora, tipo))
        cita_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return {"cita_id": cita_id, "mensaje": "Cita agendada correctamente"}
    except Exception as e:
        return {"error": str(e)}

def guardar_lead_update_bd(usuario_id, etapa=None, interes=None, notas=None):
    conn = get_db_connection()
    if not conn:
        return {"error": "No se pudo conectar a la BD"}
    
    try:
        cur = conn.cursor()
        
        # Actualizar o insertar
        cur.execute("""
            INSERT INTO leads (usuario_id, etapa, interes, timestamp)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (usuario_id) DO UPDATE SET
                etapa = COALESCE(%s, leads.etapa),
                interes = COALESCE(%s, leads.interes),
                timestamp = NOW()
        """, (usuario_id, etapa, interes, etapa, interes))
        
        conn.commit()
        cur.close()
        conn.close()
        return {"mensaje": "Lead actualizado"}
    except Exception as e:
        return {"error": str(e)}

def get_lead_context_bd(usuario_id):
    conn = get_db_connection()
    if not conn:
        return {"error": "No se pudo conectar a la BD"}
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Lead info
        cur.execute("SELECT * FROM leads WHERE usuario_id = %s", (usuario_id,))
        lead = cur.fetchone()
        
        # Conversaciones
        cur.execute("""
            SELECT rol, mensaje, timestamp FROM conversaciones 
            WHERE usuario_id = %s 
            ORDER BY timestamp DESC LIMIT 10
        """, (usuario_id,))
        conversaciones = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return {
            "lead": dict(lead) if lead else None,
            "conversaciones": [dict(c) for c in conversaciones]
        }
    except Exception as e:
        return {"error": str(e)}

# ========== PROCESAMIENTO DE HERRAMIENTAS ==========

def procesar_herramienta(nombre, params):
    """Ejecuta la herramienta solicitada por Claude"""
    if nombre == "buscar_propiedades":
        return buscar_propiedades_bd(
            desarrollo=params.get("desarrollo"),
            precio_max=params.get("precio_max"),
            recamaras=params.get("recamaras")
        )
    elif nombre == "calcular_infonavit":
        return calcular_infonavit_bd(
            salario=params.get("salario"),
            antigüedad=params.get("antigüedad", 0)
        )
    elif nombre == "agendar_cita":
        return agendar_cita_bd(
            usuario_id=params.get("usuario_id"),
            fecha=params.get("fecha"),
            hora=params.get("hora"),
            tipo=params.get("tipo", "visita")
        )
    elif nombre == "guardar_lead_update":
        return guardar_lead_update_bd(
            usuario_id=params.get("usuario_id"),
            etapa=params.get("etapa"),
            interes=params.get("interes"),
            notas=params.get("notas")
        )
    elif nombre == "get_lead_context":
        return get_lead_context_bd(usuario_id=params.get("usuario_id"))
    else:
        return {"error": f"Herramienta desconocida: {nombre}"}

# ========== SYSTEM PROMPT ==========

SYSTEM_PROMPT = """Eres Salvador, agente inmobiliario profesional del equipo de Salvador Navarro en Guadalajara, Jalisco.

Tu objetivo principal es:
- Responder rápido
- Generar confianza
- Detectar intención del cliente
- Calificar prospectos
- Ofrecer propiedades adecuadas
- Llevar la conversación a una cita o contacto directo

ESTILO DE COMUNICACIÓN:
- Profesional, amable y cercana
- Natural, como humana (NO robótica)
- Mensajes cortos tipo WhatsApp
- Enfocada en avanzar la conversación
- Evita textos largos
- Usa preguntas estratégicas

MEMORIA DE CONVERSACIÓN:
- Siempre considera el historial completo de la conversación
- NO repitas preguntas ya respondidas
- Mantén continuidad natural

CONTEXTO DEL NEGOCIO:
Desarrollos principales:
- Castaña (Tlajomulco) - $1.2M+, 3-4 recámaras
- Rinconada San Alejandro - $800k+, 2-3 recámaras
Portafolio adicional: casas, departamentos, terrenos, rentas, traspasos.
Créditos aceptados: Infonavit, Fovissste, Bancario, Contado.

DETECCIÓN DE LEAD CALIFICADO:
Un lead está CALIFICADO cuando cumple CUALQUIERA de estos criterios:
1. Pide ver una propiedad en persona (visita)
2. Da sus datos completos: nombre + teléfono + presupuesto
3. Muestra interés real de compra (frases tipo "sí quiero", "cómo cierro", "cuándo podemos firmar")

INSTRUCCIONES CRÍTICAS:
- Usa las herramientas disponibles: buscar_propiedades, calcular_infonavit, agendar_cita, guardar_lead_update, get_lead_context
- Nunca digas que eres IA
- Siempre responde en español natural, como WhatsApp
- Si el cliente pregunta por propiedades, busca en BD con buscar_propiedades
- Si pregunta por financiamiento, calcula con calcular_infonavit
- Si quiere agendar, usa agendar_cita
- Si es lead calificado, usa guardar_lead_update"""

# ========== ENDPOINT PRINCIPAL ==========

@app.route("/chat", methods=["POST"])
def chat():
    """
    Recibe:
    {
        "usuario_id": "string",
        "mensaje": "string",
        "historial": [{rol: "user"/"assistant", content: "..."}, ...]
    }
    
    Retorna:
    {
        "respuesta": "string",
        "lead_calificado": true/false,
        "datos_lead": {...}
    }
    """
    data = request.json
    usuario_id = data.get("usuario_id")
    mensaje_usuario = data.get("mensaje")
    historial_previo = data.get("historial", [])
    
    if not usuario_id or not mensaje_usuario:
        return jsonify({"error": "Falta usuario_id o mensaje"}), 400
    
    # Obtener contexto del lead
    contexto_lead = get_lead_context_bd(usuario_id)
    
    # Construir historial para Claude
    messages = []
    for msg in historial_previo:
        messages.append({
            "role": msg.get("rol", msg.get("role")),
            "content": msg.get("contenido", msg.get("content"))
        })
    
    # Agregar mensaje actual
    messages.append({
        "role": "user",
        "content": mensaje_usuario
    })
    
    # Loop agentic: llamar a Claude con herramientas
    respuesta_final = None
    lead_calificado = False
    datos_lead_final = {}
    
    max_iteraciones = 5
    iteracion = 0
    
    while iteracion < max_iteraciones:
        iteracion += 1
        
        try:
            # Llamar a Claude con herramientas
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages
            )
            
            # Procesar respuesta
            if response.stop_reason == "tool_use":
                # Claude quiere usar una herramienta
                respuesta_final = None
                
                for block in response.content:
                    if block.type == "text":
                        respuesta_final = block.text
                    elif block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input
                        tool_use_id = block.id
                        
                        # Ejecutar herramienta
                        resultado = procesar_herramienta(tool_name, tool_input)
                        
                        # Agregar a historial
                        messages.append({
                            "role": "assistant",
                            "content": response.content
                        })
                        messages.append({
                            "role": "user",
                            "content": [{
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": json.dumps(resultado)
                            }]
                        })
                
            elif response.stop_reason == "end_turn":
                # Claude terminó, extraer respuesta
                for block in response.content:
                    if block.type == "text":
                        respuesta_final = block.text
                
                # Detectar lead calificado y guardar
                if respuesta_final:
                    # Análisis simple: buscar palabras clave
                    keywords_calificado = ["agendar", "cita", "visita", "fecha", "hora", "nombre completo", "teléfono", "presupuesto"]
                    respuesta_lower = respuesta_final.lower()
                    lead_calificado = any(kw in respuesta_lower for kw in keywords_calificado)
                    
                    if lead_calificado:
                        # Intentar extraer datos básicos del mensaje
                        guardar_lead_update_bd(
                            usuario_id=usuario_id,
                            etapa="calificado",
                            interes="propiedad inmobiliaria"
                        )
                        datos_lead_final = {"usuario_id": usuario_id, "etapa": "calificado"}
                
                break
            else:
                # Stop reason desconocido
                break
        
        except Exception as e:
            respuesta_final = f"Error al procesar: {str(e)}"
            break
    
    return jsonify({
        "respuesta": respuesta_final or "No se pudo procesar",
        "lead_calificado": lead_calificado,
        "datos_lead": datos_lead_final
    })

# ========== HEALTH CHECK ==========

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "agent": "salvador"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)

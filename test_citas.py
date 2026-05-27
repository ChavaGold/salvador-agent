# Test Fase A - reparacion de citas. Correr en consola Sh: python3 test_citas.py
import app

TEL = "5213311112222"  # numero de prueba

print("=== 1. CITA VALIDA ===")
r = app.tool_agendar_visita(TEL, "2026-06-15", "10:00", nombre_cliente="Cliente Prueba", conversation_id="testA")
print(r)

print("\n=== 2. CITA SIN FECHA (Gate 2) ===")
print(app.tool_agendar_visita(TEL, "", "10:00", conversation_id="testA"))

print("\n=== 3. CITA SIN HORA (Gate 2) ===")
print(app.tool_agendar_visita(TEL, "2026-06-15", "", conversation_id="testA"))

print("\n=== 4. HORA PLACEHOLDER (Gate 2) ===")
print(app.tool_agendar_visita(TEL, "2026-06-15", "por confirmar", conversation_id="testA"))

print("\n=== 5. HORA FUERA DE RANGO ===")
print(app.tool_agendar_visita(TEL, "2026-06-15", "20:00", conversation_id="testA"))

print("\n=== 6. VERIFICAR QUE SE GUARDO REALMENTE EN BD ===")
conn = app.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT id, sender_id, nombre, telefono, fecha_cita, estatus, notas, atendida_por FROM citas WHERE telefono = %s ORDER BY id DESC LIMIT 3", (app.normalizar_telefono(TEL),))
for row in cur.fetchall():
    print(row)

print("\n=== 7. VERIFICAR PAUSA (debe estar pausada tras cita valida) ===")
print("Sandra activa para testA?:", app.sandra_esta_activa("testA", TEL))

print("\n=== 8. LIMPIAR datos de prueba ===")
cur.execute("DELETE FROM citas WHERE telefono = %s", (app.normalizar_telefono(TEL),))
conn.commit()
print("Citas de prueba borradas:", cur.rowcount)
cur.close(); conn.close()
print("\n=== FIN ===")

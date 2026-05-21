-- =====================================================================
-- SANDRA V2.0 - SETUP COMPLETO DE TABLAS
-- =====================================================================
-- Ejecutar UNA SOLA VEZ en la BD CRM
-- =====================================================================

-- ============================================================
-- 1. TABLA: sandra_admins
--    Personas que Sandra reconoce como JEFES (no como clientes)
--    Cuando escriben, entra en modo ASISTENTE personal
-- ============================================================
CREATE TABLE IF NOT EXISTS sandra_admins (
    id SERIAL PRIMARY KEY,
    telefono VARCHAR(20) UNIQUE NOT NULL,
    nombre VARCHAR(255),
    rol VARCHAR(50) DEFAULT 'admin',
    activo BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO sandra_admins (telefono, nombre, rol) VALUES
    ('5213334969274', 'Salvador Navarro', 'owner')
ON CONFLICT (telefono) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_admins_telefono ON sandra_admins(telefono);


-- ============================================================
-- 2. TABLA: sandra_blacklist
--    Contactos a los que Sandra NO debe responder como cliente
--    NOTA: El número de Salvador NO va aquí (va en sandra_admins)
-- ============================================================
CREATE TABLE IF NOT EXISTS sandra_blacklist (
    id SERIAL PRIMARY KEY,
    telefono VARCHAR(20) UNIQUE NOT NULL,
    nombre VARCHAR(255),
    motivo VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO sandra_blacklist (telefono, nombre, motivo) VALUES
    ('5213319717411', 'David Torres', 'Coordinador / Jefe corporativo'),
    ('5213330710663', 'Alvaro Belloso de Anda', 'Colega asesor'),
    ('5213326790773', 'Lupita Juarez', 'Asesora inmobiliaria'),
    ('5213326828616', 'Miguel Trigueros', 'Asesor patrimonial / Colega'),
    ('5213318530396', 'Jose Miguel Miranda', 'Coordinador'),
    ('5213319426815', 'Alejandro Carrillo', 'Colega'),
    ('5213333922930', 'Emanuel Robles', 'Colega del equipo'),
    ('5213313291917', 'Andrea', 'Asistente fiscal'),
    ('5213323232111', 'Alicia Loredo', 'Broker bancario'),
    ('5213314161697', 'Salvador Garantias Castana', 'Ingeniero Castana'),
    ('5219999031826', 'Grupo MID', 'Proveedor'),
    ('5217751449200', 'Brenda Lira Inmobiliaria', 'Colega externa'),
    ('5217713182204', 'Brenda Lira', 'Colega externa'),
    ('16452469540', 'Alejandro Alvarez Sinergeticos', 'Capacitador externo'),
    ('123456', 'EvolutionAPI', 'Sistema interno'),
    ('182304852996206', 'Salvador Navarro Bienes Raices', 'Cuenta propia')
ON CONFLICT (telefono) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_blacklist_telefono ON sandra_blacklist(telefono);


-- ============================================================
-- 3. TABLA: sandra_control
--    Pausar Sandra por conversacion individual
-- ============================================================
CREATE TABLE IF NOT EXISTS sandra_control (
    id SERIAL PRIMARY KEY,
    usuario_id VARCHAR(255) UNIQUE NOT NULL,
    activa BOOLEAN DEFAULT TRUE,
    pausada_at TIMESTAMP,
    pausada_motivo VARCHAR(255),
    reactivada_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_control_usuario ON sandra_control(usuario_id);


-- ============================================================
-- 4. TABLA: propiedades_castana
-- ============================================================
CREATE TABLE IF NOT EXISTS propiedades_castana (
    id SERIAL PRIMARY KEY,
    nivel VARCHAR(50) NOT NULL,
    precio_base NUMERIC(12,2) NOT NULL,
    precio_con_escrituras NUMERIC(12,2) NOT NULL,
    recamaras INTEGER DEFAULT 2,
    banos_completos INTEGER DEFAULT 1,
    medios_banos INTEGER DEFAULT 1,
    descripcion TEXT,
    activo BOOLEAN DEFAULT TRUE
);

INSERT INTO propiedades_castana (nivel, precio_base, precio_con_escrituras, descripcion) VALUES
    ('Segundo nivel', 678000, 698000, 'Departamento en segundo nivel, 2 recamaras, 1 bano completo + medio bano, vitropiso, boiler y tinaco. Frente a Linea 4 del Tren Ligero.'),
    ('Primer nivel', 688000, 708000, 'Departamento en primer nivel, 2 recamaras, 1 bano completo + medio bano, vitropiso, boiler y tinaco. Frente a Linea 4 del Tren Ligero.'),
    ('Planta baja', 807400, 827400, 'Departamento en planta baja, 2 recamaras, 1 bano completo + medio bano, vitropiso, boiler y tinaco. Frente a Linea 4 del Tren Ligero.')
ON CONFLICT DO NOTHING;


-- ============================================================
-- 5. Ajustar tabla LEADS - agregar campos para las 17 etapas
-- ============================================================
DO $$
BEGIN
    -- Campo: caliente (boolean)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'leads' AND column_name = 'caliente') THEN
        ALTER TABLE leads ADD COLUMN caliente BOOLEAN DEFAULT FALSE;
    END IF;

    -- Campo: telefono (texto)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'leads' AND column_name = 'telefono') THEN
        ALTER TABLE leads ADD COLUMN telefono VARCHAR(30);
    END IF;

    -- Campo: monto_credito
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'leads' AND column_name = 'monto_credito') THEN
        ALTER TABLE leads ADD COLUMN monto_credito NUMERIC(12,2);
    END IF;

    -- Campo: tipo_credito (Infonavit, Fovissste, etc)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'leads' AND column_name = 'tipo_credito') THEN
        ALTER TABLE leads ADD COLUMN tipo_credito VARCHAR(50);
    END IF;

    -- Campo: nivel_interes (planta_baja, primer_nivel, segundo_nivel, usada)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'leads' AND column_name = 'nivel_interes') THEN
        ALTER TABLE leads ADD COLUMN nivel_interes VARCHAR(50);
    END IF;

    -- Campo: fuente (facebook_ad, instagram_ad, referido, organico)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'leads' AND column_name = 'fuente') THEN
        ALTER TABLE leads ADD COLUMN fuente VARCHAR(50);
    END IF;

    -- Campo: notas
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'leads' AND column_name = 'notas') THEN
        ALTER TABLE leads ADD COLUMN notas TEXT;
    END IF;

    -- Campo: created_at
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'leads' AND column_name = 'created_at') THEN
        ALTER TABLE leads ADD COLUMN created_at TIMESTAMP DEFAULT NOW();
    END IF;

    -- Campo: updated_at
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'leads' AND column_name = 'updated_at') THEN
        ALTER TABLE leads ADD COLUMN updated_at TIMESTAMP DEFAULT NOW();
    END IF;
END $$;

-- Index para busquedas frecuentes
CREATE INDEX IF NOT EXISTS idx_leads_etapa ON leads(etapa);
CREATE INDEX IF NOT EXISTS idx_leads_caliente ON leads(caliente) WHERE caliente = TRUE;
CREATE INDEX IF NOT EXISTS idx_leads_telefono ON leads(telefono);


-- ============================================================
-- 6. TABLA: lead_etapas_historial
--    Registra cada cambio de etapa para auditoria y reportes
-- ============================================================
CREATE TABLE IF NOT EXISTS lead_etapas_historial (
    id SERIAL PRIMARY KEY,
    usuario_id VARCHAR(255) NOT NULL,
    etapa_anterior VARCHAR(50),
    etapa_nueva VARCHAR(50) NOT NULL,
    cambiado_por VARCHAR(50) DEFAULT 'sandra',
    notas TEXT,
    timestamp TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_etapas_usuario ON lead_etapas_historial(usuario_id);


-- ============================================================
-- 7. TABLA: notificaciones_pendientes
--    Cola de mensajes que Sandra debe mandar a Salvador
-- ============================================================
CREATE TABLE IF NOT EXISTS notificaciones_pendientes (
    id SERIAL PRIMARY KEY,
    destinatario VARCHAR(20) NOT NULL,
    mensaje TEXT NOT NULL,
    tipo VARCHAR(50),
    enviado BOOLEAN DEFAULT FALSE,
    enviado_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notif_pendientes ON notificaciones_pendientes(enviado) WHERE enviado = FALSE;


-- ============================================================
-- 8. Ajustes a la tabla citas
-- ============================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'citas' AND column_name = 'es_caliente') THEN
        ALTER TABLE citas ADD COLUMN es_caliente BOOLEAN DEFAULT FALSE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'citas' AND column_name = 'nombre_cliente') THEN
        ALTER TABLE citas ADD COLUMN nombre_cliente VARCHAR(255);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'citas' AND column_name = 'telefono') THEN
        ALTER TABLE citas ADD COLUMN telefono VARCHAR(30);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'citas' AND column_name = 'necesita_traslado') THEN
        ALTER TABLE citas ADD COLUMN necesita_traslado BOOLEAN DEFAULT FALSE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'citas' AND column_name = 'notas') THEN
        ALTER TABLE citas ADD COLUMN notas TEXT;
    END IF;
END $$;


-- ============================================================
-- VERIFICACION
-- ============================================================
SELECT 'sandra_admins' AS tabla, COUNT(*) AS registros FROM sandra_admins
UNION ALL
SELECT 'sandra_blacklist', COUNT(*) FROM sandra_blacklist
UNION ALL
SELECT 'sandra_control', COUNT(*) FROM sandra_control
UNION ALL
SELECT 'propiedades_castana', COUNT(*) FROM propiedades_castana
UNION ALL
SELECT 'lead_etapas_historial', COUNT(*) FROM lead_etapas_historial
UNION ALL
SELECT 'notificaciones_pendientes', COUNT(*) FROM notificaciones_pendientes;


-- ============================================================
-- REFERENCIA: ETAPAS DEL EMBUDO DE VENTA (17 ETAPAS)
-- ============================================================
-- Sandra maneja 1-4. Tu manejas 5-17 manualmente (por ahora).
--
-- 1.  nuevo_contacto         - Cliente llego de campana
-- 2.  pidio_info             - Recibio info de Castana
-- 3.  acepto_propuesta_cita  - Acepta agendar visita
-- 4.  cita_agendada          - Cita confirmada (Sandra termina aqui)
-- 5.  visita_realizada       - El cliente fue al fraccionamiento
-- 6.  firmo_solicitud        - Firma cierre de venta
-- 7.  expediente_completo    - Documentos del cliente listos
-- 8.  entregado_a_david      - Lunes/martes con corporativo
-- 9.  ingresado_infonavit    - David subio a plataforma
-- 10. ubicacion_asignada     - Corporativo asigno ubicacion (puede ser en 6 o aqui)
-- 11. aviso_retencion_emitido - Infonavit emitio notificacion (~2 sem)
-- 12. patron_acepto_acuse    - Empresa acepto en su plataforma (~3 sem)
-- 13. acuse_entregado        - Acuse entregado a corporativo (~4 sem)
-- 14. cita_notario_agendada  - Corporativo agendo firma escritura (~5 sem)
-- 15. escritura_firmada      - Cliente firmo ante notario
-- 16. entrega_llaves         - Cliente recibio llaves (~6 sem)
-- 17. venta_cerrada          - Proceso completo
-- 99. cancelado / no_califica / perdido

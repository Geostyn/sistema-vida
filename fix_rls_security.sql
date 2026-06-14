-- ============================================================
-- FIX SEGURIDAD — Habilitar RLS en todas las tablas
-- Ejecutar en: supabase.com → proyecto → SQL Editor → Run
-- Fecha: 2026-06-10
--
-- IMPORTANTE: Antes de ejecutar esto, asegúrate de haber
-- actualizado SUPABASE_KEY en Render y Streamlit al
-- service_role key (no al anon key).
-- El service_role key bypasea RLS y está en:
-- Supabase Dashboard → Project Settings → API → service_role
-- ============================================================

-- Habilitar RLS en todas las tablas
ALTER TABLE xp_state          ENABLE ROW LEVEL SECURITY;
ALTER TABLE deporte            ENABLE ROW LEVEL SECURITY;
ALTER TABLE alimentacion       ENABLE ROW LEVEL SECURITY;
ALTER TABLE gastos             ENABLE ROW LEVEL SECURITY;
ALTER TABLE habitos            ENABLE ROW LEVEL SECURITY;
ALTER TABLE diario             ENABLE ROW LEVEL SECURITY;
ALTER TABLE lexico             ENABLE ROW LEVEL SECURITY;
ALTER TABLE refranes           ENABLE ROW LEVEL SECURITY;
ALTER TABLE ideas_negocio      ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingresos           ENABLE ROW LEVEL SECURITY;
ALTER TABLE perfil_usuario     ENABLE ROW LEVEL SECURITY;
ALTER TABLE gastos_familia     ENABLE ROW LEVEL SECURITY;
ALTER TABLE items_compra       ENABLE ROW LEVEL SECURITY;
ALTER TABLE mejoras            ENABLE ROW LEVEL SECURITY;
ALTER TABLE datos_enviados     ENABLE ROW LEVEL SECURITY;
ALTER TABLE ikigai_resultado   ENABLE ROW LEVEL SECURITY;

-- Sin políticas = el anon key no puede acceder a nada.
-- El service_role key bypasea RLS automáticamente (no necesita políticas).

-- Verificar que RLS está activo en todas las tablas
SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;
-- Todas deben mostrar rowsecurity = true

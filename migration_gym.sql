-- ============================================================
-- MIGRACIÓN — Tabla gym_ejercicios (series, reps, peso)
-- Ejecutar en: supabase.com → proyecto → SQL Editor → Run
-- Fecha: 2026-06-11
--
-- Cada fila = UNA SERIE de un ejercicio.
-- El bot incrementa "serie" automáticamente cuando repites
-- el mismo ejercicio el mismo día.
-- ============================================================

create table if not exists gym_ejercicios (
  id uuid default gen_random_uuid() primary key,
  fecha date not null default current_date,
  ejercicio text not null,
  serie int not null default 1,
  reps int,
  peso numeric,
  notas text default '',
  created_at timestamptz default now()
);

-- Índice para la consulta diaria del bot (¿cuántas series lleva hoy?)
create index if not exists idx_gym_fecha_ejercicio on gym_ejercicios (fecha, ejercicio);

-- RLS activo como el resto de tablas (el service_role key lo bypasea)
alter table gym_ejercicios enable row level security;

-- Verificar
select tablename, rowsecurity from pg_tables
where schemaname = 'public' and tablename = 'gym_ejercicios';

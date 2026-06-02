-- ============================================================
-- Sistema de Vida — Supabase Schema
-- Ejecutar en: supabase.com → proyecto → SQL Editor → Run
-- ============================================================

-- 1. Estado XP (gamificación RPG)
create table if not exists xp_state (
  id integer primary key default 1,
  state_json jsonb not null default '{"total_xp":0,"skills":{},"streaks":{},"achievements_unlocked":[],"counters":{}}',
  updated_at timestamptz default now()
);
insert into xp_state (id, state_json)
  values (1, '{"total_xp":0,"skills":{},"streaks":{},"achievements_unlocked":[],"counters":{}}')
  on conflict (id) do nothing;
alter table xp_state disable row level security;

-- 2. Deporte
create table if not exists deporte (
  id uuid default gen_random_uuid() primary key,
  fecha date not null default current_date,
  actividad text,
  duracion text,
  distancia text,
  sensacion text default '😊',
  notas text,
  created_at timestamptz default now()
);
alter table deporte disable row level security;

-- 3. Alimentación
create table if not exists alimentacion (
  id uuid default gen_random_uuid() primary key,
  fecha date not null default current_date,
  desayuno text,
  comida text,
  cena text,
  snacks text,
  kcal numeric,
  prot_g numeric,
  carbs_g numeric,
  grasas_g numeric,
  agua_l numeric,
  energia text default '😊',
  created_at timestamptz default now()
);
alter table alimentacion disable row level security;

-- 4. Gastos
create table if not exists gastos (
  id uuid default gen_random_uuid() primary key,
  fecha date not null default current_date,
  categoria text not null,
  concepto text,
  importe numeric not null,
  created_at timestamptz default now()
);
alter table gastos disable row level security;

-- 5. Hábitos diarios
create table if not exists habitos (
  id uuid default gen_random_uuid() primary key,
  fecha date not null default current_date,
  ducha_fria boolean default false,
  te_clavo boolean default false,
  oracion boolean default false,
  silencio boolean default false,
  created_at timestamptz default now()
);
alter table habitos disable row level security;

-- 6. Diario personal
create table if not exists diario (
  id uuid default gen_random_uuid() primary key,
  fecha date not null default current_date,
  lo_importante text,
  gratitud text,
  mejora text,
  habitos_ok text,
  created_at timestamptz default now()
);
alter table diario disable row level security;

-- 7. Léxico
create table if not exists lexico (
  id uuid default gen_random_uuid() primary key,
  fecha date not null default current_date,
  palabra text not null,
  definicion text,
  ejemplo text,
  created_at timestamptz default now()
);
alter table lexico disable row level security;

-- 8. Refranes
create table if not exists refranes (
  id uuid default gen_random_uuid() primary key,
  fecha date not null default current_date,
  refran text not null,
  significado text,
  contexto text,
  created_at timestamptz default now()
);
alter table refranes disable row level security;

-- 9. Ideas de negocio
create table if not exists ideas_negocio (
  id uuid default gen_random_uuid() primary key,
  fecha date not null default current_date,
  idea text not null,
  inversion text,
  tiempo_monetizacion text,
  potencial text,
  estado text default '💡 Nueva',
  created_at timestamptz default now()
);
alter table ideas_negocio disable row level security;

-- ✅ Verificación: debería mostrar 9 tablas
select table_name from information_schema.tables
where table_schema = 'public'
order by table_name;

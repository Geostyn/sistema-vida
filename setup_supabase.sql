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

-- 10. Ingresos personales
create table if not exists ingresos (
  id uuid default gen_random_uuid() primary key,
  fecha date not null default current_date,
  categoria text not null,
  concepto text,
  importe numeric not null,
  created_at timestamptz default now()
);
alter table ingresos disable row level security;

-- 11. Perfil nutricional del usuario
create table if not exists perfil_usuario (
  id integer primary key default 1,
  peso numeric,
  altura numeric,
  edad integer,
  tipo_cuerpo text,
  dias_entreno integer,
  intolerancias text,
  tdee numeric,
  target_kcal numeric,
  target_prot numeric,
  target_carbs numeric,
  target_grasas numeric,
  updated_at date default current_date
);
insert into perfil_usuario (id) values (1) on conflict (id) do nothing;
alter table perfil_usuario disable row level security;

-- 11. Gastos del grupo familiar
create table if not exists gastos_familia (
  id bigserial primary key,
  fecha date not null default current_date,
  miembro text,
  concepto text,
  importe numeric not null,
  categoria text,
  origen text default 'texto',
  created_at timestamptz default now()
);
alter table gastos_familia disable row level security;

-- 12. Items de compra (extraídos de tickets/recibos)
create table if not exists items_compra (
  id bigserial primary key,
  fecha date not null default current_date,
  item_nombre text not null,
  cantidad numeric,
  precio_unitario numeric,
  total numeric,
  created_at timestamptz default now()
);
alter table items_compra disable row level security;

-- Añadir traducción a items_compra (ejecutar si la tabla ya existe)
alter table items_compra add column if not exists item_traduccion text;

-- Añadir campo creatina a habitos (ejecutar si la tabla ya existe)
alter table habitos add column if not exists creatina boolean default false;

-- 13. Mejoras de bienestar (recomendaciones científicas)
create table if not exists mejoras (
  id          bigserial primary key,
  nombre      text not null,
  categoria   text not null,  -- testosterona | estres | recuperacion | sueno | enfoque
  descripcion text,
  evidencia   text,
  estado      text not null default 'recomendada',  -- recomendada | en_proceso | activa | completada
  fecha_inicio date,
  created_at  timestamptz default now()
);
alter table mejoras disable row level security;

insert into mejoras (nombre, categoria, descripcion, evidencia) values
  ('Zinc 30mg/día', 'testosterona',
   'Tomar 30mg de zinc con la comida principal.',
   'El zinc es cofactor directo en la síntesis de testosterona. Estudios en hombres con deficiencia muestran aumentos del 15-25% en T total tras 6 semanas de suplementación.'),
  ('Vitamina D3 + K2', 'testosterona',
   'Tomar 4.000 UI de D3 + 100mcg de K2 por la mañana con grasa.',
   'La vitamina D actúa como hormona esteroide. Hombres con niveles óptimos (>40 ng/mL) tienen hasta un 25% más de testosterona libre según estudios del NEJM.'),
  ('Ashwagandha KSM-66 600mg', 'testosterona',
   'Tomar 300mg mañana y 300mg noche con comida.',
   'Meta-análisis 2023: reduce cortisol en un 28% y aumenta testosterona en un 14-17% en hombres sanos. El extracto KSM-66 es el más estudiado con 12 ensayos clínicos.'),
  ('Respiración 4-7-8', 'estres',
   'Inhalar 4s, retener 7s, exhalar 8s. Repetir 4 ciclos antes de dormir o cuando sientas estrés.',
   'Activa el sistema nervioso parasimpático en segundos. Estudios de Harvard: reduce cortisol agudo y frecuencia cardíaca. El Dr. Weil la llama el tranquilizante natural más efectivo.'),
  ('L-Teanina 200mg con café', 'estres',
   'Añadir 200mg de L-teanina a tu café matutino.',
   'Stack nootrópico validado: la teanina contrarresta el pico de ansiedad de la cafeína y prolonga el foco. RCT 2014: mejora atención sostenida y reduce cortisol pico vs cafeína sola.'),
  ('Magnesio Glicinato 400mg noche', 'estres',
   'Tomar 400mg de magnesio glicinato 30 min antes de dormir.',
   'El magnesio regula el eje HPA (cortisol). Forma glicinato es la más biodisponible y suave. Meta-análisis 2017: mejora calidad del sueño y reduce ansiedad rasgo en 6-8 semanas.'),
  ('Creatina Monohidrato 5g/día', 'recuperacion',
   'Disolver 5g en agua o batido de proteínas. Cualquier hora, lo importante es la consistencia.',
   'El suplemento más estudiado del deporte. +10-15% fuerza y potencia, +8% masa muscular en 4 semanas. También neuroprotector: estudios de 2023 muestran mejora en función cognitiva bajo estrés.'),
  ('Proteína antes de dormir', 'recuperacion',
   'Tomar 40g de proteína de caseína (o queso cottage 200g) 30 min antes de dormir.',
   'La caseína se digiere lentamente durante 7-8 horas. Estudio Maastricht: +22% síntesis proteica nocturna. Perfecta para ganar músculo sin aumentar grasa si el total calórico está controlado.'),
  ('Pantallas off 1h antes de dormir', 'sueno',
   'Apagar pantallas 1 hora antes. Leer un libro, estirar o meditar en su lugar.',
   'La luz azul suprime melatonina hasta 3 horas. Estudio Harvard Medical: usar pantallas antes de dormir retrasa el inicio del sueño 30-60 min y reduce el sueño REM un 20%.'),
  ('Habitación a 18-20°C', 'sueno',
   'Bajar la calefacción o abrir ventana para dormir a 18-20°C.',
   'La temperatura corporal debe bajar 1-2°C para iniciar el sueño profundo. Esta es la temperatura ambiente óptima. Walker (Why We Sleep): mejora sueño de ondas lentas y secreción de GH nocturna en un 70%.'),
  ('Ayuno intermitente 16:8', 'enfoque',
   'Comer solo en ventana de 8h (ej: 12:00-20:00). Ayunar las 16h restantes.',
   'El ayuno activa AMPK y SIRT1, aumenta BDNF (factor neurotrófico), mejora sensibilidad a insulina y claridad mental. Meta-análisis 2022: -4.5 kg grasa en 8 semanas sin perder músculo si la proteína es adecuada.'),
  ('Exposición solar matutina', 'enfoque',
   '10-15 min de sol directo en los ojos (sin gafas) antes de las 10:00.',
   'Regula el ritmo circadiano via retina → SCN → cortisol matutino. El Dr. Huberman: la luz matutina aumenta cortisol (pico saludable AM), mejora alerta diurna y calidad del sueño nocturno un 50%.')
on conflict do nothing;

-- 14. Registro de datos curiosos enviados (anti-repetición)
create table if not exists datos_enviados (
  id         bigserial primary key,
  titulo     text,
  categoria  text,
  slot       text,
  enviado_at timestamptz default now()
);
alter table datos_enviados disable row level security;

-- 15. Resultado del Ikigai
create table if not exists ikigai_resultado (
  id              bigserial primary key,
  fecha           date not null default current_date,
  lo_que_amas     jsonb,
  lo_que_bien     jsonb,
  mundo_necesita  jsonb,
  te_pueden_pagar jsonb,
  ikigai_central  text,
  mision          text,
  vocacion        text,
  profesion       text,
  pasion          text,
  pasos_accion    jsonb,
  reflexion_final text,
  respuestas_raw  jsonb,
  created_at      timestamptz default now()
);
alter table ikigai_resultado disable row level security;

-- ✅ Verificación: debería mostrar 15 tablas
select table_name from information_schema.tables
where table_schema = 'public'
order by table_name;

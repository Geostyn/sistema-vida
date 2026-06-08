"""
Dashboard del Sistema de Vida — Streamlit Community Cloud
Usa REST directo a Supabase (sin supabase-py) para máxima compatibilidad.
"""
import os
import json
import requests
import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from datetime import date, datetime, timedelta

st.set_page_config(
    page_title="Sistema de Vida — Geostyn",
    page_icon="⚔️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
/* Desktop base */
div[data-testid="stMetric"] { background:#1e2130; border-radius:10px; padding:14px; }
.stTabs [data-baseweb="tab"] { font-size:15px; }

/* ── Mobile responsive ── */
@media (max-width: 768px) {
  /* Reducir padding del contenedor principal */
  .block-container { padding: 0.6rem 0.6rem 1rem !important; max-width: 100% !important; }
  /* Ocultar sidebar en móvil */
  section[data-testid="stSidebar"] { display: none !important; }
  /* Encabezados más compactos */
  h1 { font-size: 1.3rem !important; }
  h2 { font-size: 1.1rem !important; }
  h3 { font-size: 1rem !important; }
  /* Tabs horizontales con scroll en móvil */
  .stTabs [data-baseweb="tab-list"] { overflow-x: auto; flex-wrap: nowrap; }
  .stTabs [data-baseweb="tab"] { font-size: 12px !important; padding: 6px 10px !important; white-space: nowrap; }
  /* Botones touch-friendly */
  .stButton > button { min-height: 44px !important; font-size: 0.95rem !important; width: 100%; }
  /* Métricas sin truncar */
  div[data-testid="stMetric"] { padding: 8px !important; }
  div[data-testid="stMetricValue"] { font-size: 1.2rem !important; }
  /* Chat input siempre visible */
  div[data-testid="stChatInput"] { position: sticky; bottom: 0; background: #0e1117; padding: 6px; }
}
</style>
""", unsafe_allow_html=True)

# ── Credenciales ──────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY") or st.secrets.get("GROQ_API_KEY", "")
GROQ_MODEL   = os.environ.get("GROQ_MODEL")   or st.secrets.get("GROQ_MODEL", "llama-3.3-70b-versatile")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Configura SUPABASE_URL y SUPABASE_KEY en los secrets de Streamlit.")
    st.stop()

# ── REST helpers ──────────────────────────────────────────────
def _headers():
    try:
        key = st.secrets["SUPABASE_KEY"]
    except Exception:
        key = SUPABASE_KEY
    return {"apikey": key, "Authorization": f"Bearer {key}"}

def _base_url():
    try:
        return st.secrets["SUPABASE_URL"]
    except Exception:
        return SUPABASE_URL

def q(table, select="*", filters=None, order=None, desc=False, limit=None):
    headers = _headers()
    base    = _base_url()
    params  = {"select": select}
    if filters:
        params.update(filters)
    if order:
        params["order"] = f"{order}.{'desc' if desc else 'asc'}"
    if limit:
        params["limit"] = str(limit)
    try:
        r = requests.get(f"{base}/rest/v1/{table}", params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "message" in data:
            st.warning(f"Supabase error en {table}: {data['message']}")
            return []
        return data
    except Exception as e:
        st.warning(f"Error cargando {table}: {e}")
        return []

def sb_patch(table, row_id, data: dict) -> bool:
    headers = {**_headers(), "Content-Type": "application/json", "Prefer": "return=minimal"}
    base    = _base_url()
    try:
        r = requests.patch(f"{base}/rest/v1/{table}?id=eq.{row_id}",
                           json=data, headers=headers, timeout=10)
        if not r.ok:
            st.warning(f"Error al guardar ({r.status_code}): {r.text[:200]}")
        return r.ok
    except Exception as e:
        st.warning(f"Error actualizando {table}: {e}")
        return False

def sb_delete(table, row_id) -> bool:
    headers = {**_headers(), "Prefer": "return=minimal"}
    base    = _base_url()
    try:
        r = requests.delete(f"{base}/rest/v1/{table}?id=eq.{row_id}",
                            headers=headers, timeout=10)
        if not r.ok:
            st.warning(f"Error al borrar ({r.status_code}): {r.text[:200]}")
        return r.ok
    except Exception as e:
        st.warning(f"Error borrando de {table}: {e}")
        return False

def sb_insert(table, data: dict) -> bool:
    headers = {**_headers(), "Content-Type": "application/json", "Prefer": "return=minimal"}
    base    = _base_url()
    try:
        r = requests.post(f"{base}/rest/v1/{table}", json=data, headers=headers, timeout=10)
        if not r.ok:
            st.warning(f"Error al insertar ({r.status_code}): {r.text[:200]}")
        return r.ok
    except Exception as e:
        st.warning(f"Error insertando en {table}: {e}")
        return False

# ── Constantes ────────────────────────────────────────────────
TODAY       = date.today().isoformat()
MONTH_START = TODAY[:8] + "01"
MONTH_LABEL = datetime.now().strftime("%B %Y")
WEEK_START  = (date.today() - timedelta(days=date.today().weekday())).isoformat()

LEVELS = [
    (0,"Principiante"),(100,"Aprendiz"),(250,"Iniciado"),
    (500,"Practicante"),(1000,"Competente"),(2000,"Experto"),
    (4000,"Maestro"),(8000,"Gran Maestro"),(15000,"Leyenda"),(30000,"Inmortal ⭐"),
]
SKILLS_INFO = {
    "fitness":    ("💪","Fitness"),
    "nutricion":  ("🥗","Nutrición"),
    "sabiduria":  ("🧠","Sabiduría"),
    "riqueza":    ("💰","Riqueza"),
    "empresario": ("🚀","Empresario"),
    "trader":     ("📈","Trader"),
    "alma":       ("🙏","Alma"),
    "disciplina": ("⚡","Disciplina"),
}

def nivel_nombre(xp):
    n = max((v for v, _ in LEVELS if xp >= v), default=0)
    return next((name for v, name in LEVELS if v == n), "Principiante")

def streak_val(streaks, key):
    v = streaks.get(key, 0)
    return v if isinstance(v, (int, float)) else v.get("current", 0)

# ── Cargar XP ─────────────────────────────────────────────────
xp_rows  = q("xp_state", select="state_json", filters={"id": "eq.1"})
xp_data  = xp_rows[0]["state_json"] if xp_rows else {}
total_xp = xp_data.get("total_xp", 0)
streaks  = xp_data.get("streaks", {})
skills   = xp_data.get("skills", {})
counters = xp_data.get("counters", {})
logros   = xp_data.get("achievements_unlocked", [])
nivel    = nivel_nombre(total_xp)

# ── Cabecera ──────────────────────────────────────────────────
st.title("⚔️ Sistema de Vida — Geostyn")
st.caption(f"Actualizado: {datetime.now().strftime('%d/%m/%Y %H:%M')} · {MONTH_LABEL}")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("🌟 Nivel Global",      nivel)
c2.metric("✨ XP Total",           f"{total_xp:,}")
c3.metric("🏆 Logros",             f"{len(logros)}/19")
c4.metric("🙏 Racha oración",      f"{streak_val(streaks,'oracion')} días")
c5.metric("🚿 Racha ducha fría",   f"{streak_val(streaks,'ducha_fria')} días")

st.divider()

# ── Tabs ──────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab_lectura, tab_mejoras, tab_proposito, tab_chat = st.tabs([
    "⚔️ Perfil RPG", "🏋️ Deporte", "🔥 Hábitos", "🍽️ Nutrición", "💶 Gastos",
    "📖 Diario & Ideas", "👨‍👩‍👧 Gastos Familia", "📚 Lectura", "💡 Mejoras", "🌸 Propósito", "💬 Chat IA"
])

# ════════════════════════════════════════════════════════════════
# TAB 1 — PERFIL RPG
# ════════════════════════════════════════════════════════════════
with tab1:
    col_r, col_s = st.columns([1, 1])

    with col_r:
        st.subheader("🌟 Nivel Global")
        _, _, xp_in, xp_next = (lambda t: t)((
            lambda xp: next(
                (( xp - LEVELS[i][0], LEVELS[i+1][0] - LEVELS[i][0] ) for i in range(len(LEVELS)-1) if xp >= LEVELS[i][0] and xp < LEVELS[i+1][0]),
                (xp - LEVELS[-1][0], 0)
            )
        )(total_xp)) if False else (0, 0, 0, 1)  # placeholder, calculo abajo

        # Calcular progreso real
        xp_in, xp_next = 0, 1
        for i in range(len(LEVELS) - 1):
            if LEVELS[i][0] <= total_xp < LEVELS[i+1][0]:
                xp_in  = total_xp - LEVELS[i][0]
                xp_next = LEVELS[i+1][0] - LEVELS[i][0]
                break
        pct = int(xp_in / xp_next * 100) if xp_next else 100
        st.progress(pct / 100, text=f"{nivel} — {xp_in}/{xp_next} XP ({pct}%)")

        st.subheader("📦 Contadores")
        cc1, cc2 = st.columns(2)
        cc1.metric("🏋️ Entrenamientos", counters.get("entrenamientos", 0))
        cc2.metric("📖 Palabras léxico",  counters.get("lexico", 0))
        cc1.metric("💬 Refranes",         counters.get("refranes", 0))
        cc2.metric("💼 Ideas negocio",    counters.get("ideas", 0))
        cc1.metric("📋 Días diario",       counters.get("dias_diario", 0))

    with col_s:
        st.subheader("📊 Radar de habilidades")
        skill_names, skill_vals = [], []
        for sk_id, (emoji, name) in SKILLS_INFO.items():
            sk = skills.get(sk_id, {"xp": 0, "level": 1})
            skill_names.append(f"{emoji} {name}")
            skill_vals.append(sk.get("level", 1))

        fig_radar = go.Figure(go.Scatterpolar(
            r=skill_vals + [skill_vals[0]],
            theta=skill_names + [skill_names[0]],
            fill="toself",
            fillcolor="rgba(76,175,80,0.25)",
            line=dict(color="#4CAF50", width=2),
        ))
        fig_radar.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0,10]), bgcolor="#0e1117"),
            paper_bgcolor="#0e1117", font=dict(color="white"),
            height=320, margin=dict(t=20,b=20),
        )
        st.plotly_chart(fig_radar, use_container_width=True)

    st.subheader("🎯 Habilidades detalladas")
    for sk_id, (emoji, name) in SKILLS_INFO.items():
        sk      = skills.get(sk_id, {"xp": 0, "level": 1})
        sk_xp   = sk.get("xp", 0)
        sk_lvl  = sk.get("level", 1)
        sk_in, sk_next = 0, 1
        for i in range(len(LEVELS) - 1):
            if LEVELS[i][0] <= sk_xp < LEVELS[i+1][0]:
                sk_in   = sk_xp - LEVELS[i][0]
                sk_next = LEVELS[i+1][0] - LEVELS[i][0]
                break
        pct_sk = int(sk_in / sk_next * 100) if sk_next else 100
        st.progress(pct_sk / 100,
                    text=f"{emoji} **{name}** — Nv.{sk_lvl} · {sk_xp:,} XP ({pct_sk}%)")

    st.subheader("🏆 Logros")
    if logros:
        st.success(f"**{len(logros)}/19** desbloqueados")
        cols_ach = st.columns(3)
        for i, a in enumerate(logros):
            cols_ach[i % 3].write(f"✅ {a}")
    else:
        st.info("Sin logros aún — ¡empieza hoy!")

# ════════════════════════════════════════════════════════════════
# TAB 2 — DEPORTE
# ════════════════════════════════════════════════════════════════
with tab2:
    dep_data = q("deporte", order="fecha", desc=True, limit=50)

    dep_mes  = [d for d in dep_data if d.get("fecha", "") >= MONTH_START]
    dep_sem  = [d for d in dep_data if d.get("fecha", "") >= WEEK_START]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("🏋️ Entrenos este mes",    len(dep_mes))
    m2.metric("🔥 Entrenos esta semana", len(dep_sem))
    m3.metric("💪 Racha entreno",        f"{streak_val(streaks,'fitness')} días")
    m4.metric("📊 Total histórico",      counters.get("entrenamientos", 0))

    if dep_data:
        df_d = pd.DataFrame(dep_data)

        # Gráfico de actividades del mes
        if dep_mes:
            actividades = pd.DataFrame(dep_mes)["actividad"].value_counts()
            fig_act = go.Figure(go.Bar(
                x=actividades.index.tolist(),
                y=actividades.values.tolist(),
                marker_color="#4CAF50",
                text=actividades.values.tolist(),
                textposition="outside",
            ))
            fig_act.update_layout(
                title=f"Actividades en {MONTH_LABEL}",
                paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                font=dict(color="white"), height=260, margin=dict(t=50,b=20),
            )
            st.plotly_chart(fig_act, use_container_width=True)

        st.subheader("📋 Historial de entrenamientos")
        cols_dep = [c for c in ["fecha","actividad","duracion","distancia","sensacion","notas"] if c in df_d.columns]
        df_show = df_d[cols_dep].copy()

        # Mostrar con expanders para ver detalle
        for _, row in df_d.head(20).iterrows():
            actividad = row.get("actividad", "—")
            fecha     = row.get("fecha", "—")
            duracion  = row.get("duracion", "") or ""
            distancia = row.get("distancia", "") or ""
            sensacion = row.get("sensacion", "") or ""
            notas     = row.get("notas", "") or ""
            label = f"📅 {fecha}  ·  {actividad}"
            if distancia: label += f"  ·  📏 {distancia}"
            if duracion:  label += f"  ·  ⏱️ {duracion}"
            with st.expander(label):
                ec1, ec2, ec3 = st.columns(3)
                ec1.write(f"**Actividad:** {actividad}")
                ec2.write(f"**Distancia:** {distancia or '—'}")
                ec3.write(f"**Duración:** {duracion or '—'}")
                ec1.write(f"**Sensación:** {sensacion or '—'}")
                if notas:
                    st.write(f"**Notas:** {notas}")
    else:
        st.info("Sin entrenamientos registrados aún.")

# ════════════════════════════════════════════════════════════════
# TAB 3 — HÁBITOS
# ════════════════════════════════════════════════════════════════
with tab3:
    hab_data = q("habitos", filters={"fecha": f"gte.{MONTH_START}"})

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("🚿 Ducha fría",  f"{streak_val(streaks,'ducha_fria')}d racha")
    s2.metric("🌿 Té de clavo", f"{streak_val(streaks,'te_clavo')}d racha")
    s3.metric("🙏 Oración",     f"{streak_val(streaks,'oracion')}d racha")
    s4.metric("🕊️ Silencio",   f"{streak_val(streaks,'silencio')}d racha")

    if hab_data:
        df_h  = pd.DataFrame(hab_data)
        days  = max(len(df_h), 1)
        counts = {
            "Ducha fría 🚿":  int(pd.to_numeric(df_h.get("ducha_fria",  pd.Series(dtype=bool)), errors="coerce").fillna(False).sum()),
            "Té de clavo 🌿": int(pd.to_numeric(df_h.get("te_clavo",    pd.Series(dtype=bool)), errors="coerce").fillna(False).sum()),
            "Oración 🙏":     int(pd.to_numeric(df_h.get("oracion",     pd.Series(dtype=bool)), errors="coerce").fillna(False).sum()),
            "Silencio 🕊️":   int(pd.to_numeric(df_h.get("silencio",    pd.Series(dtype=bool)), errors="coerce").fillna(False).sum()),
        }
        dias_mes = (date.today() - date(int(TODAY[:4]), int(TODAY[5:7]), 1)).days + 1
        colors = ["#4CAF50" if v/dias_mes>=0.7 else "#FF9800" if v/dias_mes>=0.4 else "#f44336"
                  for v in counts.values()]
        fig = go.Figure(go.Bar(
            x=list(counts.keys()), y=list(counts.values()), marker_color=colors,
            text=[f"{v}/{dias_mes}" for v in counts.values()], textposition="outside",
        ))
        fig.update_layout(
            title=f"Hábitos cumplidos en {MONTH_LABEL}",
            yaxis=dict(range=[0, dias_mes + 3]),
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="white"), height=300, margin=dict(t=50,b=20),
        )
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("📋 Registro diario")
        cols_h = [c for c in ["fecha","ducha_fria","te_clavo","oracion","silencio"] if c in df_h.columns]
        st.dataframe(
            df_h[cols_h].sort_values("fecha", ascending=False).head(30),
            hide_index=True, use_container_width=True
        )
    else:
        st.info("Sin datos de hábitos este mes aún.")

# ════════════════════════════════════════════════════════════════
# TAB 4 — NUTRICIÓN
# ════════════════════════════════════════════════════════════════
with tab4:
    # Cargar perfil nutricional personalizado
    perfil_rows = q("perfil_usuario", filters={"id": "eq.1"})
    perfil_nut  = perfil_rows[0] if perfil_rows else {}

    T_KCAL   = float(perfil_nut.get("target_kcal")  or 2800)
    T_PROT   = float(perfil_nut.get("target_prot")  or 150)
    T_CARBS  = float(perfil_nut.get("target_carbs") or 350)
    T_GRASAS = float(perfil_nut.get("target_grasas")or 78)

    # Mostrar perfil si existe
    if perfil_nut.get("peso"):
        st.subheader("🎯 Tu perfil nutricional")
        p1, p2, p3, p4, p5 = st.columns(5)
        p1.metric("⚖️ Peso",    f"{perfil_nut['peso']} kg")
        p2.metric("📏 Altura",  f"{perfil_nut['altura']} cm")
        p3.metric("💪 Tipo",    str(perfil_nut.get("tipo_cuerpo","—")).capitalize())
        p4.metric("🔥 TDEE",    f"{perfil_nut.get('tdee',0):.0f} kcal")
        p5.metric("🏋️ Días",   f"{perfil_nut.get('dias_entreno','—')} /semana")
        st.caption(f"Targets personalizados: {T_KCAL:.0f} kcal · {T_PROT:.0f}g prot · {T_CARBS:.0f}g carbos · {T_GRASAS:.0f}g grasas")
        st.divider()
    else:
        st.info("Escribe /perfil en Telegram para personalizar tus targets nutricionales.")

    # Progreso de macros de HOY
    hoy_data = q("alimentacion", filters={"fecha": f"eq.{TODAY}"})
    hoy_kcal = sum(float(r.get("kcal") or 0) for r in hoy_data)
    hoy_prot = sum(float(r.get("prot_g") or 0) for r in hoy_data)
    hoy_carbs= sum(float(r.get("carbs_g") or 0) for r in hoy_data)
    hoy_gras = sum(float(r.get("grasas_g") or 0) for r in hoy_data)

    st.subheader(f"📊 Progreso de hoy — {TODAY}")
    h1, h2, h3, h4 = st.columns(4)
    h1.metric("🔥 Kcal",    f"{hoy_kcal:.0f}", f"/{T_KCAL:.0f} — faltan {max(T_KCAL-hoy_kcal,0):.0f}")
    h2.metric("💪 Proteína",f"{hoy_prot:.0f}g", f"/{T_PROT:.0f}g — faltan {max(T_PROT-hoy_prot,0):.0f}g")
    h3.metric("🌾 Carbos",  f"{hoy_carbs:.0f}g",f"/{T_CARBS:.0f}g — faltan {max(T_CARBS-hoy_carbs,0):.0f}g")
    h4.metric("🥑 Grasas",  f"{hoy_gras:.0f}g", f"/{T_GRASAS:.0f}g — faltan {max(T_GRASAS-hoy_gras,0):.0f}g")

    macro_names  = ["🔥 Kcal", "💪 Proteína (g)", "🌾 Carbos (g)", "🥑 Grasas (g)"]
    macro_actual = [hoy_kcal, hoy_prot, hoy_carbs, hoy_gras]
    macro_target = [T_KCAL, T_PROT, T_CARBS, T_GRASAS]
    macro_pct    = [min(a/t*100, 100) if t else 0 for a, t in zip(macro_actual, macro_target)]
    fig_hoy = go.Figure()
    fig_hoy.add_trace(go.Bar(
        name="Consumido", x=macro_names, y=macro_actual,
        marker_color=["#4CAF50" if p>=90 else "#FF9800" if p>=60 else "#f44336" for p in macro_pct],
        text=[f"{p:.0f}%" for p in macro_pct], textposition="outside",
    ))
    fig_hoy.add_trace(go.Bar(
        name="Target", x=macro_names, y=macro_target,
        marker_color="rgba(255,255,255,0.12)",
    ))
    fig_hoy.update_layout(
        barmode="overlay", title="Macros de hoy vs targets",
        paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
        font=dict(color="white"), height=280, margin=dict(t=40,b=20),
    )
    st.plotly_chart(fig_hoy, use_container_width=True)
    st.divider()

    alim_data = q("alimentacion", order="fecha", desc=True, limit=30)

    if alim_data:
        df_a = pd.DataFrame(alim_data)

        avg_prot = pd.to_numeric(df_a.get("prot_g",  pd.Series()), errors="coerce").mean()
        avg_kcal = pd.to_numeric(df_a.get("kcal",    pd.Series()), errors="coerce").mean()
        avg_agua = pd.to_numeric(df_a.get("agua_l",  pd.Series()), errors="coerce").mean()
        dias_ok  = int((pd.to_numeric(df_a.get("prot_g", pd.Series()), errors="coerce") >= T_PROT).sum())

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("💪 Proteína media", f"{avg_prot:.0f}g"  if avg_prot == avg_prot else "—", f"target {T_PROT:.0f}g")
        m2.metric("🔥 Kcal media",     f"{avg_kcal:.0f}"   if avg_kcal == avg_kcal else "—", f"target {T_KCAL:.0f}")
        m3.metric("💧 Agua media",     f"{avg_agua:.1f}L"  if avg_agua == avg_agua else "—", "target 3L")
        m4.metric("✅ Días proteína OK", f"{dias_ok}/{len(df_a)}")

        fechas    = df_a.get("fecha", pd.Series())
        prot_vals = pd.to_numeric(df_a.get("prot_g", pd.Series()), errors="coerce")
        fig_prot = go.Figure()
        fig_prot.add_trace(go.Bar(
            x=fechas, y=prot_vals,
            marker_color=["#4CAF50" if (v or 0) >= T_PROT else "#f44336" for v in prot_vals],
            name="Proteína (g)",
        ))
        fig_prot.add_hline(y=T_PROT, line_dash="dash", line_color="#FF9800", annotation_text=f"Target {T_PROT:.0f}g")
        fig_prot.update_layout(
            title="Proteína diaria (últimos 30 días)",
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="white"), height=260, margin=dict(t=50,b=20),
        )
        st.plotly_chart(fig_prot, use_container_width=True)

        kcal_vals = pd.to_numeric(df_a.get("kcal", pd.Series()), errors="coerce")
        if kcal_vals.notna().any():
            fig_kcal = go.Figure(go.Scatter(
                x=fechas, y=kcal_vals, mode="lines+markers",
                line=dict(color="#FF9800", width=2), marker=dict(size=6),
            ))
            fig_kcal.add_hline(y=T_KCAL, line_dash="dash", line_color="#4CAF50", annotation_text=f"Target {T_KCAL:.0f} kcal")
            fig_kcal.update_layout(
                title="Calorías diarias",
                paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                font=dict(color="white"), height=220, margin=dict(t=50,b=20),
            )
            st.plotly_chart(fig_kcal, use_container_width=True)

        st.subheader("📋 Registro de comidas")
        for _, row in df_a.head(14).iterrows():
            fecha  = row.get("fecha", "—")
            kcal   = row.get("kcal", "") or ""
            prot   = row.get("prot_g", "") or ""
            energia= row.get("energia", "") or ""
            label  = f"📅 {fecha}"
            if kcal: label += f"  ·  🔥 {kcal} kcal"
            if prot: label += f"  ·  💪 {prot}g prot"
            with st.expander(label + f"  {energia}"):
                ac1, ac2 = st.columns(2)
                ac1.write(f"**Desayuno:** {row.get('desayuno','—') or '—'}")
                ac1.write(f"**Comida:**   {row.get('comida','—') or '—'}")
                ac1.write(f"**Cena:**     {row.get('cena','—') or '—'}")
                if row.get("snacks"): ac1.write(f"**Snacks:** {row['snacks']}")
                ac2.metric("Proteína",   f"{prot}g"  if prot  else "—")
                ac2.metric("Kcal",       f"{kcal}"   if kcal  else "—")
                ac2.metric("Carbos",     f"{row.get('carbs_g','') or '—'}g")
                ac2.metric("Grasas",     f"{row.get('grasas_g','') or '—'}g")
                ac2.metric("Agua",       f"{row.get('agua_l','') or '—'}L")
    else:
        st.info("Sin datos de nutrición aún.")

# ════════════════════════════════════════════════════════════════
# TAB 5 — GASTOS & INGRESOS
# ════════════════════════════════════════════════════════════════
with tab5:
    YEAR_START_FIN = TODAY[:4] + "-01-01"
    CAT_GASTO  = ["Comida","Gasolina","Transporte","Ocio","Ropa","Salud","Formación","Ahorro","Hogar","Servicios","Suscripciones","Extra"]
    CAT_INGRESO = ["Nómina","Freelance","Trading","Extra","Otros"]

    gastos_data   = q("gastos",   filters={"fecha": f"gte.{MONTH_START}"}, order="fecha", desc=True)
    ingresos_data = q("ingresos", filters={"fecha": f"gte.{MONTH_START}"}, order="fecha", desc=True)

    df_g = pd.DataFrame(gastos_data)   if gastos_data   else pd.DataFrame(columns=["id","fecha","categoria","concepto","importe"])
    df_i = pd.DataFrame(ingresos_data) if ingresos_data else pd.DataFrame(columns=["id","fecha","categoria","concepto","importe"])
    df_g["importe"] = pd.to_numeric(df_g["importe"], errors="coerce").fillna(0)
    df_i["importe"] = pd.to_numeric(df_i["importe"], errors="coerce").fillna(0)

    total_g   = df_g["importe"].sum()
    total_i   = df_i["importe"].sum()
    balance   = total_i - total_g

    # ── Métricas de balance ──
    st.subheader(f"💶 Balance personal — {MONTH_LABEL}")
    b1, b2, b3 = st.columns(3)
    b1.metric("💰 Ingresos",  f"€{total_i:.2f}")
    b2.metric("💸 Gastos",    f"€{total_g:.2f}")
    b3.metric("📊 Balance",   f"€{balance:.2f}",
              delta=f"{'positivo' if balance >= 0 else 'negativo'}",
              delta_color="normal" if balance >= 0 else "inverse")

    st.divider()

    # ── Histórico anual ──
    st.subheader(f"📈 Histórico {TODAY[:4]}")
    gastos_ano   = q("gastos",   select="fecha,importe,categoria", filters={"fecha": f"gte.{YEAR_START_FIN}"})
    ingresos_ano = q("ingresos", select="fecha,importe,categoria", filters={"fecha": f"gte.{YEAR_START_FIN}"})

    if gastos_ano or ingresos_ano:
        df_ga = pd.DataFrame(gastos_ano   or [])
        df_ia = pd.DataFrame(ingresos_ano or [])
        if not df_ga.empty:
            df_ga["importe"] = pd.to_numeric(df_ga["importe"], errors="coerce").fillna(0)
            df_ga["mes"] = pd.to_datetime(df_ga["fecha"]).dt.to_period("M").astype(str)
        if not df_ia.empty:
            df_ia["importe"] = pd.to_numeric(df_ia["importe"], errors="coerce").fillna(0)
            df_ia["mes"] = pd.to_datetime(df_ia["fecha"]).dt.to_period("M").astype(str)

        meses_g = df_ga.groupby("mes")["importe"].sum() if not df_ga.empty else pd.Series(dtype=float)
        meses_i = df_ia.groupby("mes")["importe"].sum() if not df_ia.empty else pd.Series(dtype=float)
        todos_meses = sorted(set(list(meses_g.index) + list(meses_i.index)))

        fig_anual = go.Figure()
        fig_anual.add_trace(go.Bar(name="Ingresos", x=todos_meses,
            y=[meses_i.get(m, 0) for m in todos_meses], marker_color="#4CAF50"))
        fig_anual.add_trace(go.Bar(name="Gastos",   x=todos_meses,
            y=[meses_g.get(m, 0) for m in todos_meses], marker_color="#f44336"))
        fig_anual.update_layout(
            barmode="group", paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="white"), height=280, margin=dict(t=20,b=20),
        )
        st.plotly_chart(fig_anual, use_container_width=True)

        # Selector de mes histórico
        meses_disp = sorted(set(list(meses_g.index) + list(meses_i.index)), reverse=True)
        mes_fin = st.selectbox("🔍 Ver detalle de mes:", meses_disp, key="sel_mes_fin")
        df_g_sel = df_ga[df_ga["mes"] == mes_fin] if not df_ga.empty else pd.DataFrame()
        df_i_sel = df_ia[df_ia["mes"] == mes_fin] if not df_ia.empty else pd.DataFrame()
        t_g_sel  = df_g_sel["importe"].sum() if not df_g_sel.empty else 0
        t_i_sel  = df_i_sel["importe"].sum() if not df_i_sel.empty else 0

        ms1, ms2, ms3 = st.columns(3)
        ms1.metric("💰 Ingresos", f"€{t_i_sel:.2f}")
        ms2.metric("💸 Gastos",   f"€{t_g_sel:.2f}")
        ms3.metric("📊 Balance",  f"€{t_i_sel - t_g_sel:.2f}")

        hcol1, hcol2 = st.columns(2)
        with hcol1:
            if not df_g_sel.empty:
                pc = df_g_sel.groupby("categoria")["importe"].sum().reset_index()
                fig_pc = go.Figure(go.Pie(labels=pc["categoria"], values=pc["importe"],
                    hole=0.4, textinfo="label+percent"))
                fig_pc.update_layout(title=f"Gastos por categoría — {mes_fin}",
                    paper_bgcolor="#0e1117", font=dict(color="white"),
                    height=300, margin=dict(t=40,b=10))
                st.plotly_chart(fig_pc, use_container_width=True)
            else:
                st.caption("Sin gastos ese mes.")
        with hcol2:
            if not df_i_sel.empty:
                pi = df_i_sel.groupby("categoria")["importe"].sum().reset_index()
                fig_pi = go.Figure(go.Bar(x=pi["importe"], y=pi["categoria"],
                    orientation="h", marker_color="#4CAF50",
                    text=[f"€{v:.0f}" for v in pi["importe"]], textposition="outside"))
                fig_pi.update_layout(title=f"Ingresos — {mes_fin}",
                    paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                    font=dict(color="white"), height=300, margin=dict(t=40,b=10,r=60))
                st.plotly_chart(fig_pi, use_container_width=True)
            else:
                st.caption("Sin ingresos ese mes.")

    st.divider()

    # ── Gráficos mes actual ──
    col_g, col_i = st.columns(2)
    with col_g:
        st.subheader("💸 Gastos este mes")
        if not df_g.empty:
            pc = df_g.groupby("categoria")["importe"].sum().reset_index().sort_values("importe", ascending=False)
            fig_g = go.Figure(go.Pie(labels=pc["categoria"], values=pc["importe"],
                hole=0.4, textinfo="label+percent"))
            fig_g.update_layout(paper_bgcolor="#0e1117", font=dict(color="white"),
                height=300, margin=dict(t=20,b=10))
            st.plotly_chart(fig_g, use_container_width=True)
        else:
            st.info("Sin gastos este mes.")
    with col_i:
        st.subheader("💰 Ingresos este mes")
        if not df_i.empty:
            pi = df_i.groupby("categoria")["importe"].sum().reset_index()
            fig_i = go.Figure(go.Bar(x=pi["importe"], y=pi["categoria"],
                orientation="h", marker_color="#4CAF50",
                text=[f"€{v:.0f}" for v in pi["importe"]], textposition="outside"))
            fig_i.update_layout(paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                font=dict(color="white"), height=300, margin=dict(t=20,b=10,r=60))
            st.plotly_chart(fig_i, use_container_width=True)
        else:
            st.info("Sin ingresos este mes.")

    st.divider()

    # ── Editores interactivos ──
    edit_tab_g, edit_tab_i, edit_tab_add = st.tabs(["✏️ Editar gastos", "✏️ Editar ingresos", "➕ Añadir"])

    with edit_tab_g:
        if not df_g.empty:
            df_edit_g = df_g[["id","fecha","categoria","concepto","importe"]].copy()
            df_edit_g.insert(0, "🗑️", False)
            edited_g = st.data_editor(df_edit_g, hide_index=True, use_container_width=True,
                num_rows="fixed", key="editor_g",
                column_config={
                    "🗑️":        st.column_config.CheckboxColumn("🗑️", width="small"),
                    "id":         st.column_config.NumberColumn("ID", disabled=True, width="small"),
                    "fecha":      st.column_config.TextColumn("Fecha", width="small"),
                    "categoria":  st.column_config.SelectboxColumn("Categoría", options=CAT_GASTO),
                    "concepto":   st.column_config.TextColumn("Concepto"),
                    "importe":    st.column_config.NumberColumn("€", format="€%.2f"),
                })
            cg1, cg2 = st.columns(2)
            with cg1:
                if st.button("💾 Guardar gastos", type="primary", use_container_width=True):
                    saved = sum(sb_patch("gastos", r["id"], {
                        "fecha": r["fecha"], "categoria": r["categoria"],
                        "concepto": r["concepto"], "importe": float(r["importe"])
                    }) for _, r in edited_g[~edited_g["🗑️"]].iterrows())
                    st.success(f"✅ {saved} guardados")
                    st.rerun()
            with cg2:
                to_del = edited_g[edited_g["🗑️"] == True]
                if len(to_del) and st.button(f"🗑️ Borrar {len(to_del)}", use_container_width=True):
                    for _, r in to_del.iterrows():
                        sb_delete("gastos", r["id"])
                    st.success("Borrado.")
                    st.rerun()
        else:
            st.info("Sin gastos este mes.")

    with edit_tab_i:
        if not df_i.empty:
            df_edit_i = df_i[["id","fecha","categoria","concepto","importe"]].copy()
            df_edit_i.insert(0, "🗑️", False)
            edited_i = st.data_editor(df_edit_i, hide_index=True, use_container_width=True,
                num_rows="fixed", key="editor_i",
                column_config={
                    "🗑️":        st.column_config.CheckboxColumn("🗑️", width="small"),
                    "id":         st.column_config.NumberColumn("ID", disabled=True, width="small"),
                    "fecha":      st.column_config.TextColumn("Fecha", width="small"),
                    "categoria":  st.column_config.SelectboxColumn("Categoría", options=CAT_INGRESO),
                    "concepto":   st.column_config.TextColumn("Concepto"),
                    "importe":    st.column_config.NumberColumn("€", format="€%.2f"),
                })
            ci1, ci2 = st.columns(2)
            with ci1:
                if st.button("💾 Guardar ingresos", type="primary", use_container_width=True):
                    saved = sum(sb_patch("ingresos", r["id"], {
                        "fecha": r["fecha"], "categoria": r["categoria"],
                        "concepto": r["concepto"], "importe": float(r["importe"])
                    }) for _, r in edited_i[~edited_i["🗑️"]].iterrows())
                    st.success(f"✅ {saved} guardados")
                    st.rerun()
            with ci2:
                to_del_i = edited_i[edited_i["🗑️"] == True]
                if len(to_del_i) and st.button(f"🗑️ Borrar {len(to_del_i)}", use_container_width=True, key="del_i"):
                    for _, r in to_del_i.iterrows():
                        sb_delete("ingresos", r["id"])
                    st.success("Borrado.")
                    st.rerun()
        else:
            st.info("Sin ingresos este mes.")

    with edit_tab_add:
        tipo_add = st.radio("Tipo", ["💸 Gasto", "💰 Ingreso"], horizontal=True)
        with st.form("form_add_fin", clear_on_submit=True):
            fa1, fa2 = st.columns(2)
            af_fecha   = fa1.text_input("Fecha", value=TODAY)
            af_importe = fa2.number_input("€ Importe", min_value=0.0, step=0.01, format="%.2f")
            fa3, fa4   = st.columns(2)
            cats       = CAT_GASTO if "Gasto" in tipo_add else CAT_INGRESO
            af_cat     = fa3.selectbox("Categoría", cats)
            af_conc    = fa4.text_input("Concepto")
            if st.form_submit_button("➕ Añadir", type="primary", use_container_width=True):
                if af_importe > 0 and af_conc:
                    tabla = "gastos" if "Gasto" in tipo_add else "ingresos"
                    ok = sb_insert(tabla, {"fecha": af_fecha, "categoria": af_cat,
                                           "concepto": af_conc, "importe": af_importe})
                    if ok:
                        st.success(f"✅ Añadido: {af_conc} €{af_importe:.2f}")
                        st.rerun()
                else:
                    st.warning("Rellena el importe y el concepto.")

# ════════════════════════════════════════════════════════════════
# TAB 6 — DIARIO & IDEAS
# ════════════════════════════════════════════════════════════════
with tab6:
    col_d, col_i = st.columns(2)

    with col_d:
        st.subheader("📖 Diario de vida")
        diario = q("diario", order="fecha", desc=True, limit=14)
        if diario:
            for e in diario:
                fecha = e.get("fecha", "")
                lo_imp = e.get("lo_importante", "") or ""
                label  = f"📅 {fecha}"
                if lo_imp: label += f" — {lo_imp[:50]}{'…' if len(lo_imp)>50 else ''}"
                with st.expander(label):
                    if e.get("lo_importante"): st.write(f"⭐ **Lo importante:** {e['lo_importante']}")
                    if e.get("gratitud"):      st.write(f"🙏 **Gratitud:** {e['gratitud']}")
                    if e.get("mejora"):        st.write(f"📈 **Mejora:** {e['mejora']}")
                    if e.get("habitos_ok"):    st.write(f"✅ **Hábitos:** {e['habitos_ok']}")
        else:
            st.info("Sin entradas de diario aún.")

        st.subheader("📚 Léxico")
        lex = q("lexico", order="fecha", desc=True, limit=20)
        if lex:
            for l in lex:
                with st.expander(f"📖 {l.get('palabra','')} — {l.get('fecha','')}"):
                    st.write(f"**Definición:** {l.get('definicion','—')}")
                    if l.get("ejemplo"): st.write(f"**Ejemplo:** _{l['ejemplo']}_")
        else:
            st.info("Sin palabras en el léxico aún.")

    with col_i:
        st.subheader("💼 Ideas de negocio")
        ideas = q("ideas_negocio", order="fecha", desc=True, limit=20)
        if ideas:
            for i in ideas:
                idea_txt = i.get("idea", "—")
                label    = f"💡 {idea_txt[:60]}{'…' if len(idea_txt)>60 else ''} — {i.get('fecha','')}"
                with st.expander(label):
                    st.write(f"**Idea:** {idea_txt}")
                    ic1, ic2 = st.columns(2)
                    ic1.write(f"**Inversión:** {i.get('inversion','—')}")
                    ic2.write(f"**Tiempo:** {i.get('tiempo_monetizacion','—')}")
                    st.write(f"**Potencial:** {i.get('potencial','—')}")
                    estado = i.get("estado", "")
                    if estado: st.write(f"**Estado:** {estado}")
        else:
            st.info("Sin ideas registradas aún.")

        st.subheader("💬 Refranes")
        refs = q("refranes", order="fecha", desc=True, limit=15)
        if refs:
            for r in refs:
                with st.expander(f"💬 {r.get('refran','')}"):
                    st.write(f"**Significado:** {r.get('significado','—')}")
                    if r.get("contexto"): st.write(f"**Contexto:** {r['contexto']}")
        else:
            st.info("Sin refranes aún.")

# ════════════════════════════════════════════════════════════════
# TAB 7 — GASTOS FAMILIA
# ════════════════════════════════════════════════════════════════
with tab7:
    st.subheader("👨‍👩‍👧 Gastos Familiares — Supermercado")

    # ── Histórico anual ──────────────────────────────────────────
    year_start = TODAY[:4] + "-01-01"
    hist_data  = q("gastos_familia", select="fecha,importe,categoria", filters={"fecha": f"gte.{year_start}"})

    if hist_data:
        df_hist = pd.DataFrame(hist_data)
        df_hist["importe"] = pd.to_numeric(df_hist["importe"], errors="coerce").fillna(0)
        df_hist["mes"]     = pd.to_datetime(df_hist["fecha"]).dt.to_period("M").astype(str)

        # Gráfico de barras por mes
        monthly = df_hist.groupby("mes")["importe"].sum().reset_index().sort_values("mes")
        fig_anual = go.Figure(go.Bar(
            x=monthly["mes"], y=monthly["importe"],
            marker_color="#4CAF50",
            text=[f"€{v:.0f}" for v in monthly["importe"]],
            textposition="outside",
        ))
        fig_anual.update_layout(
            title=f"Gasto mensual — {TODAY[:4]}",
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="white"), height=280,
            margin=dict(t=40, b=20),
            yaxis_title="€",
        )
        st.plotly_chart(fig_anual, use_container_width=True)

        # Selector de mes para ver detalle
        meses_disponibles = sorted(df_hist["mes"].unique(), reverse=True)
        mes_sel = st.selectbox("🔍 Ver detalle de un mes:", meses_disponibles, key="sel_mes_hist")

        df_mes_sel = df_hist[df_hist["mes"] == mes_sel]
        total_sel  = df_mes_sel["importe"].sum()

        hc1, hc2, hc3 = st.columns(3)
        hc1.metric("💶 Total del mes", f"€{total_sel:.2f}")
        hc2.metric("🛒 Registros",     len(df_mes_sel))
        hc3.metric("📊 Gasto medio/registro", f"€{df_mes_sel['importe'].mean():.2f}" if len(df_mes_sel) else "—")

        col_h1, col_h2 = st.columns(2)
        with col_h1:
            por_cat_sel = df_mes_sel.groupby("categoria")["importe"].sum().reset_index()
            fig_cat_sel = go.Figure(go.Pie(
                labels=por_cat_sel["categoria"], values=por_cat_sel["importe"],
                hole=0.4, textinfo="label+percent",
            ))
            fig_cat_sel.update_layout(
                title=f"Categorías — {mes_sel}",
                paper_bgcolor="#0e1117", font=dict(color="white"),
                height=300, margin=dict(t=40, b=10),
            )
            st.plotly_chart(fig_cat_sel, use_container_width=True)

        with col_h2:
            # Top items de ese mes (filtrado en Python para evitar problemas con like+%)
            ic_ano = q("items_compra", select="fecha,item_nombre,item_traduccion,total",
                       filters={"fecha": f"gte.{year_start}"})
            ic_mes = [r for r in ic_ano if str(r.get("fecha", "")).startswith(mes_sel)]
            if ic_mes:
                df_ic_mes = pd.DataFrame(ic_mes)
                df_ic_mes["total"] = pd.to_numeric(df_ic_mes["total"], errors="coerce").fillna(0)
                df_ic_mes["etiqueta"] = df_ic_mes.apply(
                    lambda r: f"{r['item_nombre']} ({r['item_traduccion']})"
                    if r.get("item_traduccion") else str(r["item_nombre"]), axis=1
                )
                top_mes = (df_ic_mes.groupby("etiqueta")["total"]
                           .sum().reset_index()
                           .sort_values("total", ascending=True).tail(8))
                fig_top = go.Figure(go.Bar(
                    x=top_mes["total"], y=top_mes["etiqueta"],
                    orientation="h", marker_color="#FF9800",
                    text=[f"€{v:.2f}" for v in top_mes["total"]],
                    textposition="outside",
                ))
                fig_top.update_layout(
                    title=f"Top items — {mes_sel}",
                    paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                    font=dict(color="white"), height=300,
                    margin=dict(t=40, b=10, l=10, r=60),
                )
                st.plotly_chart(fig_top, use_container_width=True)
            else:
                st.caption("Sin items escaneados ese mes.")

        st.divider()

    # ── Mes actual ──────────────────────────────────────────────
    gf_data = q("gastos_familia", filters={"fecha": f"gte.{MONTH_START}"}, order="fecha", desc=True)
    ic_data = q("items_compra",   filters={"fecha": f"gte.{MONTH_START}"})

    total_mes  = sum(float(r.get("importe", 0)) for r in gf_data)
    n_tickets  = len([r for r in gf_data if r.get("origen") == "foto"])
    n_registros= len(gf_data)

    fm1, fm2, fm3 = st.columns(3)
    fm1.metric("💶 Total gastado este mes", f"€{total_mes:.2f}")
    fm2.metric("🧾 Tickets escaneados",     n_tickets)
    fm3.metric("📝 Registros totales",      n_registros)

    if gf_data:
        df_gf = pd.DataFrame(gf_data)
        df_gf["importe"] = pd.to_numeric(df_gf["importe"], errors="coerce").fillna(0)

        col_izq, col_der = st.columns(2)

        with col_izq:
            st.subheader("📊 Gasto por categoría")
            por_cat = df_gf.groupby("categoria")["importe"].sum().reset_index()
            fig_cat = go.Figure(go.Pie(
                labels=por_cat["categoria"], values=por_cat["importe"],
                hole=0.4, textinfo="label+percent",
            ))
            fig_cat.update_layout(
                title=f"Total: €{total_mes:.2f}",
                paper_bgcolor="#0e1117", font=dict(color="white"),
                height=320, margin=dict(t=40,b=10),
            )
            st.plotly_chart(fig_cat, use_container_width=True)

        with col_der:
            st.subheader("🛒 Top items más comprados")
            if ic_data:
                df_ic = pd.DataFrame(ic_data)
                df_ic["total"] = pd.to_numeric(df_ic["total"], errors="coerce").fillna(0)
                df_ic["item_nombre"] = df_ic["item_nombre"].str.strip().str.title()
                # Combinar nombre original + traducción para la etiqueta del gráfico
                if "item_traduccion" in df_ic.columns:
                    df_ic["etiqueta"] = df_ic.apply(
                        lambda r: f"{r['item_nombre']} ({r['item_traduccion'].strip().lower()})"
                        if r.get("item_traduccion") else r["item_nombre"], axis=1
                    )
                else:
                    df_ic["etiqueta"] = df_ic["item_nombre"]
                top_items = (
                    df_ic.groupby("etiqueta")["total"]
                    .sum().reset_index()
                    .sort_values("total", ascending=True)
                    .tail(10)
                )
                fig_items = go.Figure(go.Bar(
                    x=top_items["total"], y=top_items["etiqueta"],
                    orientation="h", marker_color="#2196F3",
                    text=[f"€{v:.2f}" for v in top_items["total"]],
                    textposition="outside",
                ))
                fig_items.update_layout(
                    title="Top 10 por gasto — nombre original (traducción)",
                    paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                    font=dict(color="white"), height=340,
                    margin=dict(t=40,b=10,l=10,r=70),
                )
                st.plotly_chart(fig_items, use_container_width=True)

                # Tabla completa con nombre original + traducción
                st.subheader("📋 Todos los productos")
                cols_show = ["etiqueta", "total"]
                tabla_items = df_ic.groupby("etiqueta").agg(
                    total=("total", "sum"),
                    veces=("total", "count")
                ).reset_index().sort_values("total", ascending=False)
                tabla_items.columns = ["Producto (original / traducción)", "€ total", "Veces comprado"]
                st.dataframe(tabla_items, hide_index=True, use_container_width=True)
            else:
                st.caption("Aún no hay tickets escaneados este mes.")

        st.divider()

        # Evolución diaria
        st.subheader("📈 Gasto acumulado por día")
        df_gf["fecha"] = pd.to_datetime(df_gf["fecha"])
        daily = df_gf.groupby("fecha")["importe"].sum().reset_index()
        daily["acumulado"] = daily["importe"].cumsum()
        fig_evo = go.Figure()
        fig_evo.add_trace(go.Bar(name="Gasto del día", x=daily["fecha"], y=daily["importe"], marker_color="#FF9800"))
        fig_evo.add_trace(go.Scatter(name="Acumulado", x=daily["fecha"], y=daily["acumulado"], mode="lines+markers", line=dict(color="#4CAF50", width=2)))
        fig_evo.update_layout(
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="white"), height=260, margin=dict(t=20,b=20),
        )
        st.plotly_chart(fig_evo, use_container_width=True)

        st.divider()

        # ── Editor de registros ──
        st.subheader("✏️ Editar / Borrar registros")
        st.caption("Edita cualquier celda directamente y pulsa **Guardar cambios**. Marca la casilla ☑ para borrar filas.")

        df_edit = df_gf[["id","fecha","miembro","concepto","importe","categoria"]].copy()
        df_edit["fecha"] = df_edit["fecha"].dt.strftime("%Y-%m-%d")
        df_edit.insert(0, "🗑️ Borrar", False)

        edited = st.data_editor(
            df_edit,
            column_config={
                "🗑️ Borrar": st.column_config.CheckboxColumn("🗑️", width="small"),
                "id":         st.column_config.NumberColumn("ID", disabled=True, width="small"),
                "fecha":      st.column_config.TextColumn("Fecha", width="small"),
                "miembro":    st.column_config.TextColumn("Quién"),
                "concepto":   st.column_config.TextColumn("Qué"),
                "importe":    st.column_config.NumberColumn("€", format="€%.2f"),
                "categoria":  st.column_config.SelectboxColumn(
                    "Categoría",
                    options=["Comida","Limpieza","Higiene","Bebidas","Otros"],
                ),
            },
            hide_index=True,
            use_container_width=True,
            num_rows="fixed",
            key="editor_gastos",
        )

        col_save, col_del = st.columns([1, 1])
        with col_save:
            if st.button("💾 Guardar cambios", type="primary", use_container_width=True):
                saved = 0
                for _, row in edited.iterrows():
                    if not row["🗑️ Borrar"]:
                        ok = sb_patch("gastos_familia", int(row["id"]), {
                            "fecha": row["fecha"], "miembro": row["miembro"],
                            "concepto": row["concepto"], "importe": float(row["importe"]),
                            "categoria": row["categoria"],
                        })
                        if ok:
                            saved += 1
                st.success(f"✅ {saved} registros actualizados.")
                st.rerun()

        with col_del:
            to_delete = edited[edited["🗑️ Borrar"] == True]
            if len(to_delete) > 0:
                if st.button(f"🗑️ Borrar {len(to_delete)} seleccionados", type="secondary", use_container_width=True):
                    deleted = 0
                    for _, row in to_delete.iterrows():
                        if sb_delete("gastos_familia", int(row["id"])):
                            deleted += 1
                    st.success(f"🗑️ {deleted} registros borrados.")
                    st.rerun()

        st.divider()

        # ── Añadir registro manual ──
        st.subheader("➕ Añadir registro manual")
        with st.form("form_nuevo_gasto", clear_on_submit=True):
            fc1, fc2, fc3 = st.columns(3)
            nf_fecha    = fc1.text_input("Fecha (YYYY-MM-DD)", value=str(date.today()))
            nf_miembro  = fc2.text_input("Quién", placeholder="Nombre")
            nf_importe  = fc3.number_input("€ Importe", min_value=0.0, step=0.01, format="%.2f")
            fc4, fc5    = st.columns(2)
            nf_concepto = fc4.text_input("Concepto", placeholder="Lidl, Carrefour…")
            nf_categoria= fc5.selectbox("Categoría", ["Comida","Limpieza","Higiene","Bebidas","Otros"])
            submitted = st.form_submit_button("➕ Añadir", type="primary", use_container_width=True)
            if submitted:
                if nf_importe > 0 and nf_concepto:
                    ok = sb_insert("gastos_familia", {
                        "fecha": nf_fecha, "miembro": nf_miembro, "concepto": nf_concepto,
                        "importe": nf_importe, "categoria": nf_categoria, "origen": "manual",
                    })
                    if ok:
                        st.success(f"✅ Añadido: {nf_concepto} €{nf_importe:.2f}")
                        st.rerun()
                else:
                    st.warning("Rellena al menos el importe y el concepto.")

    else:
        st.info("No hay gastos familiares registrados este mes. Añade el bot al grupo de Telegram y empieza a registrar compras con texto o fotos de tickets.")

# ════════════════════════════════════════════════════════════════
# TAB 8 — LECTURA
# ════════════════════════════════════════════════════════════════
with tab_lectura:
    st.subheader("📚 Biblioteca Personal")

    libros_data = q("libros", order="titulo")
    if not libros_data:
        st.info("La biblioteca esta vacia. Ejecuta `resources/populate_libros.py` despues de que termine la descarga.")
        st.stop()

    df_lib = pd.DataFrame(libros_data)
    today_lec = str(date.today())

    # ── Metricas ──
    total_lib   = len(df_lib)
    leidos_lib  = int((df_lib["estado"] == "leido").sum())
    leyendo_lib = int((df_lib["estado"] == "leyendo").sum())
    cats_leidas = int(df_lib[df_lib["estado"] == "leido"]["categoria"].nunique()) if leidos_lib else 0

    lm1, lm2, lm3, lm4 = st.columns(4)
    lm1.metric("Biblioteca", f"{total_lib:,} libros")
    lm2.metric("Leidos", leidos_lib)
    lm3.metric("Leyendo ahora", leyendo_lib)
    lm4.metric("Categorias exploradas", cats_leidas)

    st.divider()

    # ── Libro actual ──
    leyendo_df = df_lib[df_lib["estado"] == "leyendo"]
    if len(leyendo_df) > 0:
        libro_actual = leyendo_df.iloc[0]
        st.subheader("📖 Leyendo ahora")
        lca, lcb = st.columns([3, 1])
        with lca:
            st.markdown(f"**{libro_actual['titulo']}**")
            st.caption(f"{libro_actual.get('autor') or '—'} · {libro_actual.get('categoria') or '—'}")
            if libro_actual.get("drive_url"):
                st.link_button("Abrir en Google Drive", libro_actual["drive_url"])
            elif libro_actual.get("ruta_archivo"):
                st.caption(f"`{libro_actual['ruta_archivo']}`")
        with lcb:
            rating_val = st.slider("Rating", 1, 5, int(libro_actual.get("rating") or 3), key="lec_rating")
            notas_val  = st.text_area("Notas", value=libro_actual.get("notas") or "", height=80, key="lec_notas")
            if st.button("Marcar como leido", key="lec_btn_leido"):
                ok = sb_patch("libros", int(libro_actual["id"]), {
                    "estado": "leido", "rating": rating_val,
                    "fecha_fin": today_lec, "notas": notas_val,
                })
                if ok:
                    st.success("Libro marcado como leido!")
                    st.rerun()
        st.divider()

    # ── Recomendacion semanal ──
    st.subheader("Recomendacion semanal")
    cats_disponibles = sorted(
        df_lib[df_lib["estado"] == "pendiente"]["categoria"].dropna().unique().tolist()
    )
    if cats_disponibles:
        rca, rcb = st.columns([3, 1])
        with rca:
            cat_sel = st.selectbox("Categoria", cats_disponibles, key="lec_cat")
        with rcb:
            st.write("")
            st.write("")
            btn_recom = st.button("Dame una recomendacion", key="lec_btn_recom")

        if btn_recom:
            pendientes_cat = df_lib[
                (df_lib["estado"] == "pendiente") & (df_lib["categoria"] == cat_sel)
            ]
            if len(pendientes_cat) > 0:
                rec = pendientes_cat.sample(1).iloc[0]
                st.session_state["lec_recom"] = rec.to_dict()
            else:
                st.warning(f"No hay libros pendientes en '{cat_sel}'")

        if "lec_recom" in st.session_state:
            rec = st.session_state["lec_recom"]
            st.markdown(f"""
<div style="background:#1e2130;border-radius:8px;padding:16px;border-left:4px solid #4CAF50;margin:8px 0">
<h4 style="color:#4CAF50;margin:0 0 6px 0">{rec.get('titulo','—')}</h4>
<p style="color:#ccc;margin:2px 0"><b>Autor:</b> {rec.get('autor','—')}</p>
<p style="color:#ccc;margin:2px 0"><b>Categoria:</b> {rec.get('categoria','—')}</p>
</div>
""", unsafe_allow_html=True)
            if rec.get("drive_url"):
                st.link_button("Abrir en Google Drive", rec["drive_url"])
            elif rec.get("ruta_archivo") and os.path.exists(rec["ruta_archivo"]):
                if st.button("Abrir PDF (solo desde PC)", key="lec_btn_abrir_rec"):
                    import subprocess
                    subprocess.Popen(["start", "", rec["ruta_archivo"]], shell=True)
            if st.button("Empezar a leer este libro", key="lec_btn_empezar"):
                ok = sb_patch("libros", int(rec["id"]), {
                    "estado": "leyendo", "fecha_inicio": today_lec,
                })
                if ok:
                    del st.session_state["lec_recom"]
                    st.success(f"Empezando '{rec['titulo']}'!")
                    st.rerun()
    else:
        st.info("No hay libros pendientes. Has leido toda la biblioteca!")

    st.divider()

    # ── Estadisticas ──
    if leidos_lib > 0:
        st.subheader("Progreso de lectura")
        por_cat = (
            df_lib[df_lib["estado"] == "leido"]
            .groupby("categoria").size()
            .reset_index(name="leidos")
            .sort_values("leidos", ascending=True)
        )
        if len(por_cat) > 0:
            fig_cat = go.Figure(go.Bar(
                x=por_cat["leidos"], y=por_cat["categoria"],
                orientation="h", marker_color="#4CAF50",
                text=por_cat["leidos"], textposition="outside",
            ))
            fig_cat.update_layout(
                title="Libros leidos por categoria",
                paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                font=dict(color="white"),
                height=max(200, len(por_cat) * 30),
                margin=dict(t=40, b=10, l=10, r=40),
            )
            st.plotly_chart(fig_cat, use_container_width=True)

        ultimos = (
            df_lib[df_lib["estado"] == "leido"]
            .sort_values("fecha_fin", ascending=False)
            .head(5)
        )
        st.caption("Ultimos 5 leidos:")
        cols_ult = [c for c in ["titulo", "autor", "categoria", "rating", "fecha_fin"] if c in ultimos.columns]
        st.dataframe(
            ultimos[cols_ult].rename(columns={
                "titulo": "Titulo", "autor": "Autor", "categoria": "Categoria",
                "rating": "Rating", "fecha_fin": "Fecha fin",
            }),
            hide_index=True, use_container_width=True,
        )
        st.divider()

    # ── Biblioteca completa ──
    st.subheader("Biblioteca completa")
    filtro_cats = st.multiselect(
        "Filtrar por categoria",
        sorted(df_lib["categoria"].dropna().unique().tolist()),
        key="lec_filtro_cats",
    )
    filtro_estado = st.radio(
        "Estado", ["Todos", "Pendiente", "Leyendo", "Leido"], horizontal=True, key="lec_filtro_estado"
    )
    df_filtrado = df_lib.copy()
    if filtro_cats:
        df_filtrado = df_filtrado[df_filtrado["categoria"].isin(filtro_cats)]
    if filtro_estado != "Todos":
        estado_map = {"Pendiente": "pendiente", "Leyendo": "leyendo", "Leido": "leido"}
        df_filtrado = df_filtrado[df_filtrado["estado"] == estado_map[filtro_estado]]

    st.caption(f"Mostrando {len(df_filtrado):,} libros")
    df_mostrar = df_filtrado.copy()
    df_mostrar["Abrir"] = df_mostrar["drive_url"].fillna("") if "drive_url" in df_mostrar.columns else ""
    cols_show = [c for c in ["titulo", "autor", "categoria", "estado", "rating", "fecha_fin"] if c in df_mostrar.columns]
    st.dataframe(
        df_mostrar[cols_show + ["Abrir"]].rename(columns={
            "titulo": "Titulo", "autor": "Autor", "categoria": "Categoria",
            "estado": "Estado", "rating": "Rating", "fecha_fin": "Terminado",
        }),
        column_config={
            "Abrir": st.column_config.LinkColumn("Abrir", display_text="Abrir libro"),
        },
        hide_index=True, use_container_width=True,
    )

# ════════════════════════════════════════════════════════════════
# TAB MEJORAS — Sistema de recomendaciones científicas
# ════════════════════════════════════════════════════════════════

def _sb_update_mejora(mejora_id: int, estado: str, fecha_inicio: str = None):
    payload = {"estado": estado}
    if fecha_inicio:
        payload["fecha_inicio"] = fecha_inicio
    headers = {**_headers(), "Content-Type": "application/json", "Prefer": "return=minimal"}
    requests.patch(
        f"{_base_url()}/rest/v1/mejoras?id=eq.{mejora_id}",
        json=payload, headers=headers, timeout=10,
    )


CAT_EMOJI = {
    "testosterona": "⚡", "estres": "🧘", "recuperacion": "💪",
    "sueno": "😴", "enfoque": "🎯",
}

with tab_mejoras:
    st.subheader("💡 Mejoras de Bienestar")
    st.caption("Recomendaciones basadas en evidencia científica. Ve añadiéndolas a tu rutina.")

    mejoras_data = q("mejoras", order="id")

    en_proceso   = [m for m in mejoras_data if m.get("estado") in ("en_proceso", "activa")]
    recomendadas = [m for m in mejoras_data if m.get("estado") == "recomendada"]
    completadas  = [m for m in mejoras_data if m.get("estado") == "completada"]

    # ── Mejoras en proceso ───────────────────────────────────────
    if en_proceso:
        st.markdown("### 🔄 En proceso")
        n_cols = min(len(en_proceso), 3)
        cols_ep = st.columns(n_cols)
        for idx, m in enumerate(en_proceso):
            with cols_ep[idx % n_cols]:
                dias = 0
                if m.get("fecha_inicio"):
                    try:
                        dias = (date.today() - date.fromisoformat(m["fecha_inicio"])).days
                    except Exception:
                        pass
                cat_e = CAT_EMOJI.get(m.get("categoria", ""), "💡")
                estado_badge = "✅ Activa" if m["estado"] == "activa" else "🔄 En proceso"
                st.markdown(
                    f"**{cat_e} {m['nombre']}**  \n"
                    f"{estado_badge} · {dias} días  \n"
                    f"<small>{m.get('descripcion','')}</small>",
                    unsafe_allow_html=True,
                )
                with st.expander("🔬 Evidencia científica"):
                    st.write(m.get("evidencia", "—"))
                if m["estado"] == "en_proceso":
                    if st.button("✅ Marcar como activa", key=f"activa_{m['id']}"):
                        _sb_update_mejora(m["id"], "activa")
                        st.success("¡Consolidada como hábito activo!")
                        st.rerun()
                if st.button("🏁 Completar", key=f"completar_{m['id']}"):
                    _sb_update_mejora(m["id"], "completada")
                    st.success("¡Completada!")
                    st.rerun()
        st.divider()

    # ── Generar nueva recomendación con IA ───────────────────────
    st.markdown("### 🤖 Nueva recomendación IA")
    st.caption("La IA analiza tu perfil y lo que ya tienes para sugerirte algo nuevo y personalizado.")

    if st.button("✨ Generar recomendación personalizada", use_container_width=True):
        if not GROQ_API_KEY:
            st.error("Añade GROQ_API_KEY en los secrets de Streamlit.")
        else:
            with st.spinner("Pensando en algo nuevo para ti..."):
                existing_names = [m["nombre"] for m in mejoras_data]
                existing_str = ", ".join(existing_names) if existing_names else "ninguna todavía"
                racha_ducha = streak_val(streaks, "ducha_fria")
                racha_creatina_gen = streak_val(streaks, "creatina")
                prompt_gen = (
                    f"El usuario es un hombre joven con objetivo de ganar masa muscular.\n"
                    f"Hábitos actuales: ducha fría ({racha_ducha}d), té de clavo, oración, "
                    f"creatina ({racha_creatina_gen}d).\n"
                    f"Mejoras que ya tiene: {existing_str}.\n\n"
                    f"Genera UNA nueva mejora de bienestar que NO esté en esa lista. "
                    f"Puede ser de cualquier área: testosterona, recuperación, sueño, estrés, "
                    f"enfoque, nutrición, salud hormonal, rendimiento físico...\n\n"
                    f'Devuelve SOLO un JSON: {{"nombre": "...", "categoria": "testosterona|estres|recuperacion|sueno|enfoque", '
                    f'"descripcion": "instrucción práctica 1-2 frases", '
                    f'"evidencia": "explicación científica con datos reales 2-3 frases"}}'
                )
                try:
                    import re as _re
                    resp_gen = requests.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                        json={"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt_gen}],
                              "max_tokens": 400, "temperature": 0.85},
                        timeout=30,
                    )
                    resp_gen.raise_for_status()
                    raw = resp_gen.json()["choices"][0]["message"]["content"].strip()
                    match_gen = _re.search(r'\{.*\}', raw, _re.DOTALL)
                    if match_gen:
                        nueva = json.loads(match_gen.group())
                        st.session_state["nueva_mejora_ai"] = nueva
                    else:
                        st.error("No pude parsear la respuesta. Intenta de nuevo.")
                except Exception as e_gen:
                    st.error(f"Error: {e_gen}")

    if "nueva_mejora_ai" in st.session_state:
        nueva = st.session_state["nueva_mejora_ai"]
        cat_e2 = CAT_EMOJI.get(nueva.get("categoria", ""), "💡")
        st.success(f"**{cat_e2} {nueva['nombre']}** [{nueva.get('categoria','').upper()}]")
        st.write(nueva.get("descripcion", ""))
        with st.expander("🔬 Evidencia científica"):
            st.write(nueva.get("evidencia", ""))
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("➕ Añadir a mis mejoras", use_container_width=True):
                ok = sb_insert("mejoras", {
                    "nombre": nueva["nombre"],
                    "categoria": nueva.get("categoria", "enfoque"),
                    "descripcion": nueva.get("descripcion", ""),
                    "evidencia": nueva.get("evidencia", ""),
                    "estado": "en_proceso",
                    "fecha_inicio": TODAY,
                })
                if ok:
                    del st.session_state["nueva_mejora_ai"]
                    st.success("✅ Añadida a tus mejoras en proceso.")
                    st.rerun()
        with col_btn2:
            if st.button("🔄 Generar otra", use_container_width=True):
                del st.session_state["nueva_mejora_ai"]
                st.rerun()

    # ── Mejoras guardadas disponibles (recomendadas antiguas) ────
    if recomendadas:
        st.divider()
        st.markdown("### 📋 Recomendaciones guardadas")
        for m in recomendadas:
            with st.expander(f"{CAT_EMOJI.get(m.get('categoria',''),'💡')} {m['nombre']}"):
                st.write(m.get("descripcion", ""))
                st.markdown(f"🔬 **¿Por qué funciona?**  \n_{m.get('evidencia', '')}_")
                if st.button("➕ Quiero intentarlo", key=f"intentar_{m['id']}"):
                    _sb_update_mejora(m["id"], "en_proceso", TODAY)
                    st.success(f"✅ **{m['nombre']}** añadida a tus mejoras en proceso.")
                    st.rerun()

    # ── Completadas ──────────────────────────────────────────────
    if completadas:
        with st.expander(f"🏆 Completadas ({len(completadas)})"):
            for m in completadas:
                st.markdown(f"✅ ~~{m['nombre']}~~")

    # ── Racha creatina ───────────────────────────────────────────
    st.divider()
    racha_creatina = streak_val(streaks, "creatina")
    col_cr1, col_cr2 = st.columns(2)
    col_cr1.metric("💊 Racha Creatina", f"{racha_creatina} días")
    STREAK_MULTS = [(60, 2.0), (30, 1.75), (14, 1.5), (7, 1.25), (0, 1.0)]
    mult_cr = next((m for t, m in STREAK_MULTS if racha_creatina >= t), 1.0)
    col_cr2.metric("⚡ Multiplicador XP actual", f"×{mult_cr:.2f}")
    if racha_creatina < 7:
        st.progress(racha_creatina / 7, text=f"1ª semana: {racha_creatina}/7 días → próximo: ×1.25")
    elif racha_creatina < 14:
        st.progress((racha_creatina - 7) / 7, text=f"2ª semana: {racha_creatina - 7}/7 días → próximo: ×1.50")
    elif racha_creatina < 30:
        st.progress((racha_creatina - 14) / 16, text=f"Primer mes: {racha_creatina - 14}/16 días → próximo: ×1.75")
    elif racha_creatina < 60:
        st.progress((racha_creatina - 30) / 30, text=f"2 meses: {racha_creatina - 30}/30 días → próximo: ×2.00")
    else:
        st.success("🔥 ¡Máximo multiplicador desbloqueado! ×2.00 en todos los hábitos")

# ════════════════════════════════════════════════════════════════
# TAB PROPÓSITO — Ikigai
# ════════════════════════════════════════════════════════════════
with tab_proposito:
    st.subheader("🌸 Tu Propósito — Ikigai")
    st.caption("El _ikigai_ (生き甲斐) es el concepto japonés de tu \"razón de ser\": la intersección de lo que amas, en lo que eres bueno, lo que el mundo necesita y por lo que te pueden pagar.")

    ikigai_rows = q("ikigai_resultado", order="id", desc=True)
    ik = ikigai_rows[0] if ikigai_rows else {}

    if not ik:
        st.info("Todavía no has completado tu Ikigai. Escribe **/ikigai** en Telegram y sigue las 10 preguntas guiadas.")
    else:
        fecha_ik = ik.get("fecha", "—")
        st.caption(f"Último análisis: {fecha_ik}")

        # ── Ikigai central ───────────────────────────────────────
        ikigai_central = ik.get("ikigai_central", "")
        if ikigai_central:
            st.markdown(
                f"""<div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);
                border-radius:16px;padding:24px 28px;margin:16px 0;color:white;text-align:center;">
                <div style="font-size:2em;margin-bottom:8px;">🌟</div>
                <div style="font-size:1.1em;font-weight:600;line-height:1.5;">{ikigai_central}</div>
                </div>""",
                unsafe_allow_html=True,
            )

        st.divider()

        # ── Los 4 círculos ───────────────────────────────────────
        st.markdown("### Los 4 Círculos")
        col_a, col_b = st.columns(2)
        col_c, col_d = st.columns(2)

        def _render_circulo(col, emoji, titulo, color, items):
            items = items if isinstance(items, list) else ([items] if items else [])
            with col:
                st.markdown(
                    f"""<div style="background:{color};border-radius:12px;padding:16px;min-height:140px;">
                    <b style="font-size:1.05em;">{emoji} {titulo}</b><br><br>
                    {"".join(f"• {i}<br>" for i in items)}
                    </div>""",
                    unsafe_allow_html=True,
                )

        _render_circulo(col_a, "❤️", "Lo que AMAS",       "#fff0f3", ik.get("lo_que_amas",     []))
        _render_circulo(col_b, "💪", "En lo que eres BUENO", "#f0fff4", ik.get("lo_que_bien",  []))
        _render_circulo(col_c, "🌍", "Lo que el MUNDO NECESITA", "#f0f4ff", ik.get("mundo_necesita",  []))
        _render_circulo(col_d, "💰", "Por lo que te PUEDEN PAGAR", "#fffbf0", ik.get("te_pueden_pagar", []))

        st.divider()

        # ── Intersecciones ───────────────────────────────────────
        st.markdown("### ✨ Las 4 Intersecciones")
        col_m1, col_m2 = st.columns(2)
        col_m3, col_m4 = st.columns(2)

        def _inter_card(col, emoji, titulo, subtitulo, texto):
            with col:
                st.markdown(
                    f"""<div style="border:1px solid #e0e0e0;border-radius:10px;padding:14px;margin-bottom:4px;">
                    <b>{emoji} {titulo}</b><br>
                    <small style="color:#888;">{subtitulo}</small><br><br>
                    <span style="font-size:0.95em;">{texto}</span>
                    </div>""",
                    unsafe_allow_html=True,
                )

        _inter_card(col_m1, "🎯", "MISIÓN",    "amor + mundo",      ik.get("mision", "—"))
        _inter_card(col_m2, "🎸", "VOCACIÓN",  "amor + habilidad",  ik.get("vocacion", "—"))
        _inter_card(col_m3, "💼", "PROFESIÓN", "habilidad + dinero", ik.get("profesion", "—"))
        _inter_card(col_m4, "🔥", "PASIÓN",    "amor + dinero",     ik.get("pasion", "—"))

        st.divider()

        # ── Pasos de acción ──────────────────────────────────────
        pasos = ik.get("pasos_accion", [])
        if isinstance(pasos, list) and pasos:
            st.markdown("### 🚀 Pasos de Acción")
            for i, paso in enumerate(pasos, 1):
                st.markdown(f"**{i}.** {paso}")

        # ── Reflexión final ──────────────────────────────────────
        reflexion = ik.get("reflexion_final", "")
        if reflexion:
            st.divider()
            st.markdown("### 💭 Reflexión Final")
            st.markdown(f"_{reflexion}_")

        # ── Historial ────────────────────────────────────────────
        if len(ikigai_rows) > 1:
            st.divider()
            with st.expander(f"📅 Historial de análisis ({len(ikigai_rows)} en total)"):
                for row in ikigai_rows[1:]:
                    fecha_r = row.get("fecha", "—")
                    central_r = row.get("ikigai_central", "")
                    st.markdown(f"**{fecha_r}** — _{central_r}_")

        # ── CTA Telegram ─────────────────────────────────────────
        st.divider()
        st.info("💡 Para actualizar tu Ikigai escribe **/ikigai** en Telegram y sigue las 10 preguntas guiadas.")


# ════════════════════════════════════════════════════════════════
# TAB CHAT IA — Conversación libre con contexto del sistema de vida
# ════════════════════════════════════════════════════════════════

def _build_life_context() -> str:
    try:
        nivel_c = nivel_nombre(xp_data.get("total_xp", 0))
        streaks_c = xp_data.get("streaks", {})

        hab_mes = q("habitos", filters={"fecha": f"gte.{MONTH_START}"})
        dias_mes = max(1, len(hab_mes))
        hab_pct = {}
        for hab in ["ducha_fria", "te_clavo", "oracion", "creatina"]:
            count = sum(1 for r in hab_mes if r.get(hab))
            hab_pct[hab] = f"{count}/{dias_mes}"

        alim_hoy = q("alimentacion", filters={"fecha": f"eq.{TODAY}"})
        kcal_hoy = sum(float(r.get("kcal") or 0) for r in alim_hoy)
        prot_hoy = sum(float(r.get("prot_g") or 0) for r in alim_hoy)

        mej_all = q("mejoras")
        mej_activas = [m["nombre"] for m in mej_all if m.get("estado") in ("activa", "en_proceso")]

        return (
            f"Nivel: {nivel_c} ({xp_data.get('total_xp', 0):,} XP) | "
            f"Rachas: ducha={streak_val(streaks_c,'ducha_fria')}d "
            f"oracion={streak_val(streaks_c,'oracion')}d "
            f"creatina={streak_val(streaks_c,'creatina')}d | "
            f"Hábitos este mes: ducha={hab_pct.get('ducha_fria','?')} "
            f"oracion={hab_pct.get('oracion','?')} "
            f"creatina={hab_pct.get('creatina','?')} | "
            f"Macros hoy: {kcal_hoy:.0f} kcal / {prot_hoy:.0f}g prot | "
            f"Mejoras activas: {', '.join(mej_activas) or 'ninguna'}"
        )
    except Exception:
        return "Estado no disponible."


def _call_groq_chat(prompt: str, context: str, history: list) -> str:
    if not GROQ_API_KEY:
        return "⚠️ Configura GROQ_API_KEY en los secrets de Streamlit para usar el chat IA."
    system_prompt = (
        "Eres el asistente personal de Geostyn, experto en su Sistema de Vida.\n"
        f"Estado actual: {context}\n\n"
        "Responde en español de forma directa y útil. "
        "Si el usuario pregunta sobre sus datos, úsalos. "
        "Si das consejos de salud o fitness, fundamenta con evidencia. "
        "Sé amigable y motivador. Máximo 250 palabras."
    )
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history[-10:]:
        if msg["role"] in ("user", "assistant"):
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": prompt})
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": GROQ_MODEL, "messages": messages, "max_tokens": 500, "temperature": 0.7},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"❌ Error al conectar con la IA: {e}"


with tab_chat:
    st.subheader("💬 Chat con tu Asistente IA")
    st.caption("Hazme cualquier pregunta sobre tu sistema de vida, salud, nutrición o hábitos.")

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    if st.session_state.chat_history:
        if st.button("🗑️ Limpiar conversación", key="clear_chat"):
            st.session_state.chat_history = []
            st.rerun()

    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Pregúntame algo... ej: ¿cuántas kcal llevo hoy?"):
        st.session_state.chat_history.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Pensando..."):
                context = _build_life_context()
                response = _call_groq_chat(prompt, context, st.session_state.chat_history[:-1])
            st.markdown(response)

        st.session_state.chat_history.append({"role": "assistant", "content": response})

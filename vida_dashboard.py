"""
Dashboard del Sistema de Vida — Streamlit Community Cloud
Usa REST directo a Supabase (sin supabase-py) para máxima compatibilidad.
"""
import os
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
div[data-testid="stMetric"] { background:#1e2130; border-radius:10px; padding:14px; }
.stTabs [data-baseweb="tab"] { font-size:15px; }
</style>
""", unsafe_allow_html=True)

# ── Credenciales ──────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Configura SUPABASE_URL y SUPABASE_KEY en los secrets de Streamlit.")
    st.stop()

# ── REST helpers ──────────────────────────────────────────────
def _sb_creds():
    try:
        return st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"]
    except Exception:
        return SUPABASE_URL, SUPABASE_KEY

def q(table, select="*", filters=None, order=None, desc=False, limit=None):
    url, key = _sb_creds()
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    params = {"select": select}
    if filters:
        params.update(filters)
    if order:
        params["order"] = f"{order}.{'desc' if desc else 'asc'}"
    if limit:
        params["limit"] = str(limit)
    try:
        r = requests.get(f"{url}/rest/v1/{table}", params=params, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.warning(f"Error cargando {table}: {e}")
        return []

def sb_patch(table, row_id, data: dict) -> bool:
    url, key = _sb_creds()
    headers = {
        "apikey": key, "Authorization": f"Bearer {key}",
        "Content-Type": "application/json", "Prefer": "return=minimal",
    }
    try:
        r = requests.patch(f"{url}/rest/v1/{table}?id=eq.{row_id}", json=data, headers=headers, timeout=10)
        return r.ok
    except Exception as e:
        st.warning(f"Error actualizando {table}: {e}")
        return False

def sb_delete(table, row_id) -> bool:
    url, key = _sb_creds()
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "return=minimal"}
    try:
        r = requests.delete(f"{url}/rest/v1/{table}?id=eq.{row_id}", headers=headers, timeout=10)
        return r.ok
    except Exception as e:
        st.warning(f"Error borrando de {table}: {e}")
        return False

def sb_insert(table, data: dict) -> bool:
    url, key = _sb_creds()
    headers = {
        "apikey": key, "Authorization": f"Bearer {key}",
        "Content-Type": "application/json", "Prefer": "return=minimal",
    }
    try:
        r = requests.post(f"{url}/rest/v1/{table}", json=data, headers=headers, timeout=10)
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
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "⚔️ Perfil RPG", "🏋️ Deporte", "🔥 Hábitos", "🍽️ Nutrición", "💶 Gastos", "📖 Diario & Ideas", "👨‍👩‍👧 Gastos Familia"
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
# TAB 5 — GASTOS
# ════════════════════════════════════════════════════════════════
with tab5:
    gastos_data = q("gastos", filters={"fecha": f"gte.{MONTH_START}"})

    if gastos_data:
        df_g = pd.DataFrame(gastos_data)
        df_g["importe"] = pd.to_numeric(df_g["importe"], errors="coerce").fillna(0)
        total   = df_g["importe"].sum()
        por_cat = df_g.groupby("categoria")["importe"].sum().reset_index().sort_values("importe", ascending=False)

        gm1, gm2, gm3 = st.columns(3)
        gm1.metric(f"💶 Total {MONTH_LABEL}", f"€{total:.2f}")
        gm2.metric("🛒 Nº transacciones",     len(df_g))
        gm3.metric("📊 Mayor gasto",           f"€{df_g['importe'].max():.2f}" if len(df_g) else "—")

        col1, col2 = st.columns([1, 1])
        with col1:
            fig_pie = go.Figure(go.Pie(
                labels=por_cat["categoria"], values=por_cat["importe"],
                hole=0.45, textinfo="label+percent",
            ))
            fig_pie.update_layout(
                paper_bgcolor="#0e1117", font=dict(color="white"),
                height=320, margin=dict(t=20,b=10),
            )
            st.plotly_chart(fig_pie, use_container_width=True)
        with col2:
            st.dataframe(
                por_cat.rename(columns={"categoria":"Categoría","importe":"€"}),
                hide_index=True, use_container_width=True
            )

        st.subheader("📋 Transacciones del mes")
        cols_g = [c for c in ["fecha","categoria","concepto","importe"] if c in df_g.columns]
        st.dataframe(
            df_g[cols_g].sort_values("fecha", ascending=False),
            hide_index=True, use_container_width=True
        )
    else:
        st.info("Sin gastos este mes aún.")

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

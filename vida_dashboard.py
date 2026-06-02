"""
Dashboard del Sistema de Vida — Streamlit Community Cloud
Lee datos de Supabase. Configura SUPABASE_URL y SUPABASE_KEY en los secrets.
"""
import os
import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from datetime import date, datetime

st.set_page_config(
    page_title="Sistema de Vida — Geostyn",
    page_icon="⚔️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
div[data-testid="stMetric"] { background:#1e2130; border-radius:10px; padding:14px; }
</style>
""", unsafe_allow_html=True)

# ── Conexión Supabase ─────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Configura SUPABASE_URL y SUPABASE_KEY en los secrets de Streamlit.")
    st.stop()

@st.cache_resource
def get_sb():
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)

sb = get_sb()

# ── Helpers ───────────────────────────────────────────────────
TODAY       = date.today().isoformat()
MONTH_START = TODAY[:8] + "01"
MONTH_LABEL = datetime.now().strftime("%B %Y")

LEVELS = [
    (0,"Principiante"),(500,"Aprendiz"),(1500,"Iniciado"),
    (3000,"Practicante"),(6000,"Competente"),(10000,"Experto"),
    (15000,"Maestro"),(22000,"Gran Maestro"),(30000,"Leyenda"),(40000,"Inmortal ⭐"),
]
SKILLS_INFO = {
    "fitness":     ("💪","Fitness"),
    "nutricion":   ("🥗","Nutrición"),
    "sabiduria":   ("🧠","Sabiduría"),
    "riqueza":     ("💰","Riqueza"),
    "empresario":  ("🚀","Empresario"),
    "trader":      ("📈","Trader"),
    "alma":        ("🙏","Alma"),
    "disciplina":  ("⚡","Disciplina"),
}

def nivel_nombre(xp):
    n = max((v for v, _ in LEVELS if xp >= v), default=0)
    return next((name for v, name in LEVELS if v == n), "Principiante"), n

@st.cache_data(ttl=120)
def load_xp():
    r = sb.table("xp_state").select("state_json").eq("id", 1).execute()
    return r.data[0]["state_json"] if r.data else {}

@st.cache_data(ttl=120)
def load_month(table, cols="*"):
    return sb.table(table).select(cols).gte("fecha", MONTH_START).order("fecha").execute().data or []

@st.cache_data(ttl=120)
def load_last(table, cols="*", limit=30):
    return sb.table(table).select(cols).order("fecha", desc=True).limit(limit).execute().data or []

# ── Cargar datos ──────────────────────────────────────────────
xp_data   = load_xp()
total_xp  = xp_data.get("total_xp", 0)
streaks   = xp_data.get("streaks", {})
skills    = xp_data.get("skills", {})
logros    = xp_data.get("achievements_unlocked", [])
nivel, _  = nivel_nombre(total_xp)

# ── Cabecera ──────────────────────────────────────────────────
st.title("⚔️ Sistema de Vida Personal")
st.caption(f"Actualizado: {datetime.now().strftime('%d/%m/%Y %H:%M')} · Mes: {MONTH_LABEL}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("🌟 Nivel Global", nivel)
c2.metric("✨ XP Total", f"{total_xp:,}")
c3.metric("🏆 Logros", f"{len(logros)}/19")
c4.metric("📅 Racha oración", f"{streaks.get('oracion',{}).get('current',0)} días")

st.divider()

# ── Tabs ──────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "⚔️ Perfil RPG", "🔥 Hábitos", "🍽️ Nutrición", "💶 Gastos", "📖 Historial"
])

# ── TAB 1: Perfil RPG ─────────────────────────────────────────
with tab1:
    st.subheader("Habilidades")

    skill_names, skill_vals, skill_colors = [], [], []
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
        name="Nivel",
    ))
    fig_radar.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, range=[0, 10], tickfont=dict(size=9)),
            bgcolor="#0e1117",
        ),
        paper_bgcolor="#0e1117", font=dict(color="white"),
        height=380, margin=dict(t=20, b=20),
    )
    st.plotly_chart(fig_radar, use_container_width=True)

    # Tabla de habilidades
    rows = []
    for sk_id, (emoji, name) in SKILLS_INFO.items():
        sk = skills.get(sk_id, {"xp": 0, "level": 1})
        lvl = sk.get("level", 1)
        xp  = sk.get("xp", 0)
        rows.append({"Habilidad": f"{emoji} {name}", "Nivel": lvl, "XP": f"{xp:,}"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # Logros
    st.subheader("🏆 Logros desbloqueados")
    if logros:
        st.success(f"Has desbloqueado **{len(logros)}/19** logros: {', '.join(logros)}")
    else:
        st.info("Aún sin logros — ¡empieza hoy!")

# ── TAB 2: Hábitos ────────────────────────────────────────────
with tab2:
    hab_data = load_month("habitos")

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("🚿 Ducha fría", f"{streaks.get('ducha_fria',{}).get('current',0)} días racha")
    s2.metric("🌿 Té de clavo", f"{streaks.get('te_clavo',{}).get('current',0)} días racha")
    s3.metric("🙏 Oración", f"{streaks.get('oracion',{}).get('current',0)} días racha")
    s4.metric("🕊️ Silencio", f"{streaks.get('silencio',{}).get('current',0)} días racha")

    if hab_data:
        df_hab = pd.DataFrame(hab_data)
        days_elapsed = (date.today() - date(int(TODAY[:4]), int(TODAY[5:7]), 1)).days + 1
        counts = {
            "Ducha fría 🚿": int(df_hab.get("ducha_fria", pd.Series(dtype=bool)).sum()),
            "Té de clavo 🌿": int(df_hab.get("te_clavo", pd.Series(dtype=bool)).sum()),
            "Oración 🙏": int(df_hab.get("oracion", pd.Series(dtype=bool)).sum()),
            "Silencio 🕊️": int(df_hab.get("silencio", pd.Series(dtype=bool)).sum()),
        }
        colors = ["#4CAF50" if v/days_elapsed >= 0.7 else "#FF9800" if v/days_elapsed >= 0.4 else "#f44336"
                  for v in counts.values()]
        fig_hab = go.Figure(go.Bar(
            x=list(counts.keys()), y=list(counts.values()),
            marker_color=colors,
            text=[f"{v}/{days_elapsed}" for v in counts.values()],
            textposition="outside",
        ))
        fig_hab.update_layout(
            title=f"Hábitos cumplidos en {MONTH_LABEL}",
            yaxis=dict(range=[0, days_elapsed + 3]),
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="white"), height=300, margin=dict(t=50, b=20),
        )
        st.plotly_chart(fig_hab, use_container_width=True)
    else:
        st.info("Sin datos de hábitos este mes aún.")

# ── TAB 3: Nutrición ──────────────────────────────────────────
with tab3:
    alim_data = load_last("alimentacion", limit=14)

    TARGET_PROT = 150
    TARGET_KCAL = 2800

    if alim_data:
        df_alim = pd.DataFrame(alim_data)

        # Métricas promedio
        avg_prot = df_alim["prot_g"].dropna().mean() if "prot_g" in df_alim else 0
        avg_kcal = df_alim["kcal"].dropna().mean() if "kcal" in df_alim else 0
        avg_agua = df_alim["agua_l"].dropna().mean() if "agua_l" in df_alim else 0
        dias_ok  = int((df_alim["prot_g"].dropna() >= TARGET_PROT).sum()) if "prot_g" in df_alim else 0

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("💪 Proteína media", f"{avg_prot:.0f}g", f"target {TARGET_PROT}g")
        m2.metric("🔥 Kcal media", f"{avg_kcal:.0f}", f"target {TARGET_KCAL}")
        m3.metric("💧 Agua media", f"{avg_agua:.1f}L", "target 3L")
        m4.metric("✅ Días proteína OK", f"{dias_ok}/{len(df_alim)}")

        # Gráfico proteína
        fig_prot = go.Figure()
        if "prot_g" in df_alim:
            fig_prot.add_trace(go.Bar(
                name="Proteína (g)", x=df_alim["fecha"], y=df_alim["prot_g"],
                marker_color=["#4CAF50" if (v or 0) >= TARGET_PROT else "#f44336"
                               for v in df_alim["prot_g"]],
            ))
            fig_prot.add_hline(y=TARGET_PROT, line_dash="dash", line_color="#FF9800",
                                annotation_text=f"Target {TARGET_PROT}g")
        fig_prot.update_layout(
            title="Proteína diaria (verde = objetivo cumplido)",
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="white"), height=280, margin=dict(t=50, b=20),
        )
        st.plotly_chart(fig_prot, use_container_width=True)

        # Tabla últimas entradas
        st.subheader("Últimas entradas")
        cols_show = [c for c in ["fecha","desayuno","comida","cena","kcal","prot_g","agua_l","energia"]
                     if c in df_alim.columns]
        st.dataframe(df_alim[cols_show].head(7), hide_index=True, use_container_width=True)
    else:
        st.info("Sin datos de nutrición aún. Dicta tu día al bot de Telegram.")

# ── TAB 4: Gastos ─────────────────────────────────────────────
with tab4:
    gastos_data = load_month("gastos")

    if gastos_data:
        df_g = pd.DataFrame(gastos_data)
        total = df_g["importe"].sum()
        por_cat = df_g.groupby("categoria")["importe"].sum().reset_index().sort_values("importe", ascending=False)

        st.metric(f"💶 Total gastado en {MONTH_LABEL}", f"€{total:.2f}")

        c1, c2 = st.columns([1, 1])
        with c1:
            fig_pie = go.Figure(go.Pie(
                labels=por_cat["categoria"], values=por_cat["importe"],
                hole=0.45, textinfo="label+percent",
                marker=dict(colors=["#4CAF50","#2196F3","#FF9800","#E91E63",
                                     "#9C27B0","#00BCD4","#FF5722","#607D8B","#795548"]),
            ))
            fig_pie.update_layout(
                paper_bgcolor="#0e1117", font=dict(color="white"),
                height=320, margin=dict(t=20, b=10),
            )
            st.plotly_chart(fig_pie, use_container_width=True)

        with c2:
            st.dataframe(
                por_cat.rename(columns={"categoria":"Categoría","importe":"€"}),
                hide_index=True, use_container_width=True,
            )

        # Últimos gastos
        st.subheader("Últimos gastos")
        cols_g = [c for c in ["fecha","categoria","concepto","importe"] if c in df_g.columns]
        st.dataframe(df_g[cols_g].tail(10).sort_values("fecha", ascending=False),
                     hide_index=True, use_container_width=True)
    else:
        st.info("Sin gastos registrados este mes aún.")

# ── TAB 5: Historial ──────────────────────────────────────────
with tab5:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("🏋️ Últimos entrenamientos")
        dep = load_last("deporte", limit=10)
        if dep:
            df_dep = pd.DataFrame(dep)
            cols_d = [c for c in ["fecha","actividad","duracion","sensacion","notas"] if c in df_dep.columns]
            st.dataframe(df_dep[cols_d], hide_index=True, use_container_width=True)
        else:
            st.info("Sin entrenamientos aún.")

        st.subheader("📖 Léxico reciente")
        lex = load_last("lexico", limit=10)
        if lex:
            st.dataframe(pd.DataFrame(lex)[["fecha","palabra","definicion"]], hide_index=True, use_container_width=True)
        else:
            st.info("Sin palabras aún.")

    with col2:
        st.subheader("📖 Diario reciente")
        diario = load_last("diario", limit=7)
        if diario:
            for entry in diario:
                with st.expander(f"📅 {entry.get('fecha','')}"):
                    if entry.get("lo_importante"): st.write(f"⭐ {entry['lo_importante']}")
                    if entry.get("gratitud"):      st.write(f"🙏 {entry['gratitud']}")
                    if entry.get("mejora"):        st.write(f"📈 {entry['mejora']}")
        else:
            st.info("Sin entradas de diario aún.")

        st.subheader("💼 Ideas de negocio")
        ideas = load_last("ideas_negocio", limit=5)
        if ideas:
            for i in ideas:
                st.write(f"💡 **{i.get('idea','')}** — {i.get('estado','')}")
        else:
            st.info("Sin ideas capturadas aún.")

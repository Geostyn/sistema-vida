"""
Dashboard del Sistema de Vida — Streamlit Community Cloud
Usa REST directo a Supabase (sin supabase-py) para máxima compatibilidad.
"""
import os
import requests
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

# ── Credenciales ──────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Configura SUPABASE_URL y SUPABASE_KEY en los secrets de Streamlit.")
    st.stop()

# ── REST helper ───────────────────────────────────────────────
@st.cache_data(ttl=120)
def sb(table, _url, _key, select="*", filters=None, order=None, desc=False, limit=None):
    """Llama directamente a la API REST de Supabase.
    _url y _key con prefijo _ se pasan a la función pero no se usan como clave de caché."""
    headers = {
        "apikey": _key,
        "Authorization": f"Bearer {_key}",
    }
    params = {"select": select}
    if filters:
        params.update(filters)
    if order:
        params["order"] = f"{order}.{'desc' if desc else 'asc'}"
    if limit:
        params["limit"] = str(limit)
    try:
        r = requests.get(
            f"{_url}/rest/v1/{table}",
            params=params, headers=headers, timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.warning(f"Error cargando {table}: {e}")
        return []

def q(table, **kwargs):
    """Wrapper que inyecta URL y KEY automáticamente."""
    return sb(table, SUPABASE_URL, SUPABASE_KEY, **kwargs)

# ── Constantes ────────────────────────────────────────────────
TODAY       = date.today().isoformat()
MONTH_START = TODAY[:8] + "01"
MONTH_LABEL = datetime.now().strftime("%B %Y")

LEVELS = [
    (0,"Principiante"),(500,"Aprendiz"),(1500,"Iniciado"),
    (3000,"Practicante"),(6000,"Competente"),(10000,"Experto"),
    (15000,"Maestro"),(22000,"Gran Maestro"),(30000,"Leyenda"),(40000,"Inmortal ⭐"),
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

# ── Cargar datos ──────────────────────────────────────────────
xp_rows  = q("xp_state", select="state_json", filters={"id": "eq.1"})
xp_data  = xp_rows[0]["state_json"] if xp_rows else {}
total_xp = xp_data.get("total_xp", 0)
streaks  = xp_data.get("streaks", {})
skills   = xp_data.get("skills", {})
logros   = xp_data.get("achievements_unlocked", [])
nivel    = nivel_nombre(total_xp)

# ── Cabecera ──────────────────────────────────────────────────
st.title("⚔️ Sistema de Vida Personal")
st.caption(f"Actualizado: {datetime.now().strftime('%d/%m/%Y %H:%M')} · Mes: {MONTH_LABEL}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("🌟 Nivel Global", nivel)
c2.metric("✨ XP Total", f"{total_xp:,}")
c3.metric("🏆 Logros", f"{len(logros)}/19")
c4.metric("🙏 Racha oración", f"{streaks.get('oracion',{}).get('current',0)} días")

st.divider()

# ── Tabs ──────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "⚔️ Perfil RPG", "🔥 Hábitos", "🍽️ Nutrición", "💶 Gastos", "📖 Historial"
])

# ── TAB 1: Perfil RPG ─────────────────────────────────────────
with tab1:
    st.subheader("Habilidades")
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
        height=380, margin=dict(t=20,b=20),
    )
    st.plotly_chart(fig_radar, use_container_width=True)

    rows = [{"Habilidad": f"{e} {n}", "Nivel": skills.get(k,{}).get("level",1), "XP": f"{skills.get(k,{}).get('xp',0):,}"}
            for k,(e,n) in SKILLS_INFO.items()]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    st.subheader("🏆 Logros")
    if logros:
        st.success(f"**{len(logros)}/19** desbloqueados: {', '.join(logros)}")
    else:
        st.info("Sin logros aún — ¡empieza hoy!")

# ── TAB 2: Hábitos ────────────────────────────────────────────
with tab2:
    hab_data = q("habitos", filters={"fecha": f"gte.{MONTH_START}"})

    s1,s2,s3,s4 = st.columns(4)
    s1.metric("🚿 Ducha fría",  f"{streaks.get('ducha_fria',{}).get('current',0)}d racha")
    s2.metric("🌿 Té de clavo", f"{streaks.get('te_clavo',{}).get('current',0)}d racha")
    s3.metric("🙏 Oración",     f"{streaks.get('oracion',{}).get('current',0)}d racha")
    s4.metric("🕊️ Silencio",   f"{streaks.get('silencio',{}).get('current',0)}d racha")

    if hab_data:
        df_h = pd.DataFrame(hab_data)
        days = (date.today() - date(int(TODAY[:4]), int(TODAY[5:7]), 1)).days + 1
        counts = {
            "Ducha fría 🚿":  int(df_h.get("ducha_fria", pd.Series(dtype=bool)).sum()),
            "Té de clavo 🌿": int(df_h.get("te_clavo",   pd.Series(dtype=bool)).sum()),
            "Oración 🙏":     int(df_h.get("oracion",    pd.Series(dtype=bool)).sum()),
            "Silencio 🕊️":   int(df_h.get("silencio",   pd.Series(dtype=bool)).sum()),
        }
        colors = ["#4CAF50" if v/days>=0.7 else "#FF9800" if v/days>=0.4 else "#f44336"
                  for v in counts.values()]
        fig = go.Figure(go.Bar(
            x=list(counts.keys()), y=list(counts.values()), marker_color=colors,
            text=[f"{v}/{days}" for v in counts.values()], textposition="outside",
        ))
        fig.update_layout(
            title=f"Hábitos en {MONTH_LABEL}",
            yaxis=dict(range=[0,days+3]),
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="white"), height=300, margin=dict(t=50,b=20),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Sin datos de hábitos este mes aún.")

# ── TAB 3: Nutrición ──────────────────────────────────────────
with tab3:
    alim_data = q("alimentacion", order="fecha", desc=True, limit=14)

    if alim_data:
        df_a = pd.DataFrame(alim_data)
        avg_prot = pd.to_numeric(df_a.get("prot_g",  pd.Series()), errors="coerce").mean()
        avg_kcal = pd.to_numeric(df_a.get("kcal",    pd.Series()), errors="coerce").mean()
        avg_agua = pd.to_numeric(df_a.get("agua_l",  pd.Series()), errors="coerce").mean()
        dias_ok  = int((pd.to_numeric(df_a.get("prot_g", pd.Series()), errors="coerce") >= 150).sum())

        m1,m2,m3,m4 = st.columns(4)
        m1.metric("💪 Proteína media", f"{avg_prot:.0f}g" if avg_prot==avg_prot else "—", "target 150g")
        m2.metric("🔥 Kcal media",     f"{avg_kcal:.0f}"  if avg_kcal==avg_kcal else "—", "target 2800")
        m3.metric("💧 Agua media",     f"{avg_agua:.1f}L" if avg_agua==avg_agua else "—", "target 3L")
        m4.metric("✅ Días proteína OK", f"{dias_ok}/{len(df_a)}")

        prot_vals = pd.to_numeric(df_a.get("prot_g", pd.Series()), errors="coerce")
        fig = go.Figure(go.Bar(
            x=df_a.get("fecha", pd.Series()), y=prot_vals,
            marker_color=["#4CAF50" if (v or 0)>=150 else "#f44336" for v in prot_vals],
        ))
        fig.add_hline(y=150, line_dash="dash", line_color="#FF9800", annotation_text="Target 150g")
        fig.update_layout(
            title="Proteína diaria",
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="white"), height=280, margin=dict(t=50,b=20),
        )
        st.plotly_chart(fig, use_container_width=True)

        cols_show = [c for c in ["fecha","desayuno","comida","cena","kcal","prot_g","agua_l","energia"] if c in df_a]
        st.dataframe(df_a[cols_show].head(7), hide_index=True, use_container_width=True)
    else:
        st.info("Sin datos de nutrición aún.")

# ── TAB 4: Gastos ─────────────────────────────────────────────
with tab4:
    gastos_data = q("gastos", filters={"fecha": f"gte.{MONTH_START}"})

    if gastos_data:
        df_g = pd.DataFrame(gastos_data)
        df_g["importe"] = pd.to_numeric(df_g["importe"], errors="coerce").fillna(0)
        total   = df_g["importe"].sum()
        por_cat = df_g.groupby("categoria")["importe"].sum().reset_index().sort_values("importe", ascending=False)

        st.metric(f"💶 Total en {MONTH_LABEL}", f"€{total:.2f}")

        col1, col2 = st.columns(2)
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
            st.dataframe(por_cat.rename(columns={"categoria":"Categoría","importe":"€"}),
                         hide_index=True, use_container_width=True)

        cols_g = [c for c in ["fecha","categoria","concepto","importe"] if c in df_g]
        st.dataframe(df_g[cols_g].sort_values("fecha", ascending=False).head(10),
                     hide_index=True, use_container_width=True)
    else:
        st.info("Sin gastos este mes aún.")

# ── TAB 5: Historial ──────────────────────────────────────────
with tab5:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("🏋️ Entrenamientos")
        dep = q("deporte", order="fecha", desc=True, limit=10)
        if dep:
            df_d = pd.DataFrame(dep)
            st.dataframe(df_d[[c for c in ["fecha","actividad","duracion","sensacion"] if c in df_d]],
                         hide_index=True, use_container_width=True)
        else:
            st.info("Sin entrenamientos aún.")

        st.subheader("📖 Léxico")
        lex = q("lexico", order="fecha", desc=True, limit=8)
        if lex:
            st.dataframe(pd.DataFrame(lex)[["fecha","palabra","definicion"]],
                         hide_index=True, use_container_width=True)
        else:
            st.info("Sin palabras aún.")

    with col2:
        st.subheader("📖 Diario")
        diario = q("diario", order="fecha", desc=True, limit=7)
        if diario:
            for e in diario:
                with st.expander(f"📅 {e.get('fecha','')}"):
                    if e.get("lo_importante"): st.write(f"⭐ {e['lo_importante']}")
                    if e.get("gratitud"):      st.write(f"🙏 {e['gratitud']}")
                    if e.get("mejora"):        st.write(f"📈 {e['mejora']}")
        else:
            st.info("Sin entradas de diario aún.")

        st.subheader("💼 Ideas de negocio")
        ideas = q("ideas_negocio", order="fecha", desc=True, limit=5)
        if ideas:
            for i in ideas:
                st.write(f"💡 **{i.get('idea','')}** — {i.get('estado','')}")
        else:
            st.info("Sin ideas aún.")

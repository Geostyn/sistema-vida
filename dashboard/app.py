"""
Dashboard Streamlit - Panel de analisis en tiempo real.
Ejecutar con: streamlit run dashboard/app.py
"""
import sys
import os
import json
import glob
import sqlite3
import pandas as pd
from datetime import datetime, timezone, timedelta

import streamlit as st
import plotly.graph_objects as go

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from streamlit_autorefresh import st_autorefresh
    _HAS_AUTOREFRESH = True
except ImportError:
    _HAS_AUTOREFRESH = False

st.set_page_config(
    page_title="Trading System XAUUSD",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

if _HAS_AUTOREFRESH:
    st_autorefresh(interval=60_000, limit=None, key="dashboard_refresh")

st.markdown("""
<style>
div[data-testid="stMetric"] { background:#1e2130; border-radius:8px; padding:12px; }
.buy-card  { border-left:4px solid #4CAF50; background:#1a2e1a; border-radius:8px; padding:12px; margin:6px 0; }
.sell-card { border-left:4px solid #f44336; background:#2e1a1a; border-radius:8px; padding:12px; margin:6px 0; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ──────────────────────────────────────────────────

def load_market_state():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "market_state.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def get_db_path():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "trades.db")

def load_recent_signals(hours=4):
    db = get_db_path()
    if not os.path.exists(db):
        return pd.DataFrame()
    try:
        conn   = sqlite3.connect(db)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        df     = pd.read_sql_query(
            "SELECT * FROM signals WHERE timestamp >= ? ORDER BY timestamp DESC",
            conn, params=(cutoff,))
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()

def load_all_signals(limit=500):
    db = get_db_path()
    if not os.path.exists(db):
        return pd.DataFrame()
    try:
        conn = sqlite3.connect(db)
        df   = pd.read_sql_query(
            f"SELECT * FROM signals ORDER BY timestamp DESC LIMIT {limit}", conn)
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()

def _logs_dir():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")

def load_backtest_metrics():
    path = os.path.join(_logs_dir(), "backtest_metrics.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def load_backtest_equity():
    path = os.path.join(_logs_dir(), "backtest_equity.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def load_backtest_trades():
    pattern = os.path.join(_logs_dir(), "backtest_*.csv")
    files   = sorted(glob.glob(pattern), reverse=True)
    if not files:
        return pd.DataFrame()
    try:
        return pd.read_csv(files[0])
    except Exception:
        return pd.DataFrame()

def time_ago(ts):
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        mins = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
        if mins < 60:
            return f"hace {mins}m"
        if mins < 1440:
            return f"hace {mins//60}h"
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return str(ts)


# ── Cargar datos ──────────────────────────────────────────────

state         = load_market_state()
last_update   = state.get("last_update", "")
symbols_state = state.get("symbols", {})
news_state    = state.get("news", {})
risk_state    = state.get("risk", {})
news_calendar = state.get("news_calendar", [])
recent_df     = load_recent_signals(hours=4)
active_count  = len(recent_df)


# ── Header ────────────────────────────────────────────────────

st.title("📊 Trading System — XAUUSD | EUR/USD | USD Pairs")

if last_update:
    st.caption(f"Actualizado: {time_ago(last_update)}  |  Sistema activo ✅")
else:
    st.warning("⚠️  main.py no esta corriendo. Ejecuta INICIAR.bat")

# Metricas rapidas
c1, c2, c3, c4, c5 = st.columns(5)

xau   = symbols_state.get("XAUUSD", {})
eur   = symbols_state.get("EURUSD", {})
bk    = news_state.get("blackout", False)
pnl   = risk_state.get("daily_pnl", 0)

c1.metric("XAUUSD",           f"${xau.get('price','--')}",    xau.get("bias_h4", ""))
c2.metric("EURUSD",           str(eur.get("price", "--")),    eur.get("bias_h4", ""))
c3.metric("Señales (4h)",     active_count)
c4.metric("Noticias",         "🔴 BLACKOUT" if bk else "🟢 OK",  news_state.get("next_event_name","")[:22])
c5.metric("P&L del dia",      f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}")

st.divider()


# ── Tabs ──────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📈 Analisis de Mercado",
    "🎯 Señales Activas",
    "📰 Noticias",
    "📊 Historial",
    "🧪 Backtest",
    "⚔️ Sistema de Vida",
])


# ── TAB 1: Analisis ───────────────────────────────────────────
with tab1:
    st.subheader("Estado del Mercado")

    if not symbols_state:
        st.info("Esperando datos... Asegurate de que INICIAR.bat este corriendo.")
    else:
        rows = []
        for sym, data in symbols_state.items():
            rows.append({
                "Simbolo":      sym,
                "Precio":       data.get("price", "--"),
                "Bias H4":      data.get("bias_h4", "--"),
                "Tendencia H1": data.get("structure_h1", "--"),
                "ATR":          data.get("atr", "--"),
                "RSI":          data.get("rsi", "--"),
                "Ultima señal": time_ago(data["last_signal_time"]) if data.get("last_signal_time") else "Sin señales",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True)

        st.subheader("Order Blocks Detectados (H1)")
        for sym, data in symbols_state.items():
            obs = [ob for ob in data.get("order_blocks", []) if ob.get("valid", False)]
            if not obs:
                continue
            st.markdown(f"**{sym}** — {len(obs)} OB validos")
            ob_rows = []
            for ob in obs[:6]:
                ob_rows.append({
                    "Tipo":     ob.get("type", ""),
                    "Superior": ob.get("top", ""),
                    "Inferior": ob.get("bottom", ""),
                    "Dist %":   f"{ob.get('distance_pct', 0):.2f}%",
                })
            st.dataframe(pd.DataFrame(ob_rows), hide_index=True)

        # Grafico de sesgo
        if len(symbols_state) > 1:
            st.subheader("Sesgo por Simbolo")
            bias_vals  = {
                sym: 1 if d.get("bias_h4") == "BULLISH"
                       else (-1 if d.get("bias_h4") == "BEARISH" else 0)
                for sym, d in symbols_state.items()
            }
            colors = ["#4CAF50" if v > 0 else ("#f44336" if v < 0 else "#9e9e9e")
                      for v in bias_vals.values()]
            labels = ["BULLISH" if v > 0 else ("BEARISH" if v < 0 else "NEUTRAL")
                      for v in bias_vals.values()]
            fig = go.Figure(go.Bar(
                x=list(bias_vals.keys()), y=list(bias_vals.values()),
                marker_color=colors, text=labels, textposition="auto",
            ))
            fig.update_layout(
                height=280,
                yaxis=dict(tickvals=[-1,0,1], ticktext=["BEARISH","NEUTRAL","BULLISH"]),
                paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                font=dict(color="white"), margin=dict(t=20, b=20),
            )
            st.plotly_chart(fig)


# ── TAB 2: Señales ────────────────────────────────────────────
with tab2:
    st.subheader("Señales de las ultimas 4 horas")

    if recent_df.empty:
        st.info("El sistema esta buscando setups de alta probabilidad. Las alertas llegaran a tu Telegram.")
    else:
        for _, row in recent_df.iterrows():
            direction = str(row.get("direction", ""))
            color     = "#1a2e1a" if direction == "BUY" else "#2e1a1a"
            icon      = "🟢" if direction == "BUY" else "🔴"
            sym       = str(row.get("symbol", ""))
            ts        = str(row.get("timestamp", ""))
            sent      = row.get("sent_telegram", 0)

            st.markdown(
                f'<div style="background:{color};border-radius:8px;padding:10px;margin:6px 0">'
                f'<b>{icon} {sym} | {direction} | {time_ago(ts)}'
                f'{"  📤 Telegram" if sent else ""}</b></div>',
                unsafe_allow_html=True,
            )

            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Entrada",    f"{float(row.get('entry',0)):.5f}")
            col2.metric("Stop Loss",  f"{float(row.get('sl',0)):.5f}")
            col3.metric("TP1",        f"{float(row.get('tp1',0)):.5f}")
            col4.metric("R:R",        f"1:{float(row.get('rr',0)):.1f}")
            col5.metric("Confianza",  f"{float(row.get('confidence',0)):.0%}")

            if row.get("lot_size"):
                st.caption(f"Lotes sugeridos: {float(row['lot_size']):.2f}")
            if row.get("news_warning"):
                st.warning(f"⚠️  {row['news_warning']}")
            st.divider()


# ── TAB 3: Noticias ───────────────────────────────────────────
with tab3:
    st.subheader("Calendario Economico — Alto Impacto")

    if news_state.get("blackout"):
        st.error(f"🔴 NO OPERAR — {news_state.get('reason', 'Noticia proxima')}")
    elif news_calendar:
        mins_to_next = None
        for ev in news_calendar:
            try:
                ev_t = datetime.fromisoformat(ev.get("time", ""))
                if ev_t.tzinfo is None:
                    ev_t = ev_t.replace(tzinfo=timezone.utc)
                diff = (ev_t - datetime.now(timezone.utc)).total_seconds() / 60
                if diff > 0:
                    mins_to_next = int(diff)
                    next_name    = ev.get("event", "")
                    break
            except Exception:
                pass

        if mins_to_next is not None and mins_to_next < 60:
            st.warning(f"🟡 PRECAUCION — '{next_name}' en {mins_to_next} minutos")
        else:
            st.success("🟢 TRADING OK — Sin noticias en la proxima hora")

        rows = []
        for ev in news_calendar:
            try:
                ev_t     = datetime.fromisoformat(ev.get("time", ""))
                if ev_t.tzinfo is None:
                    ev_t = ev_t.replace(tzinfo=timezone.utc)
                time_str = ev_t.strftime("%H:%M UTC")
                diff     = (ev_t - datetime.now(timezone.utc)).total_seconds() / 60
                estado   = "Pasado" if diff < 0 else f"En {int(diff)}min"
            except Exception:
                time_str = ev.get("time","")[:16]
                estado   = "--"

            rows.append({
                "Hora":    time_str,
                "Pais":    ev.get("country",""),
                "Evento":  ev.get("event",""),
                "Estimado": str(ev.get("estimate","--")),
                "Estado":  estado,
            })

        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True)
    else:
        st.success("🟢 TRADING OK — Sin eventos de alto impacto")
        st.info("Configura tu API key de Finnhub en config.yaml para ver el calendario en tiempo real.")


# ── TAB 4: Historial ─────────────────────────────────────────
with tab4:
    st.subheader("Historial de Señales")
    all_df = load_all_signals()

    if all_df.empty:
        st.info("Las señales se van guardando automaticamente aqui.")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total señales", len(all_df))

        if "outcome" in all_df.columns:
            with_result = all_df[all_df["outcome"].isin(["WIN","LOSS"])]
            if not with_result.empty:
                wins     = len(with_result[with_result["outcome"] == "WIN"])
                losses   = len(with_result[with_result["outcome"] == "LOSS"])
                win_rate = wins / len(with_result)
                m2.metric("Con resultado", len(with_result))
                m3.metric("Win Rate",   f"{win_rate:.1%}")
                m4.metric("W / L",      f"{wins} / {losses}")

                # Curva de equity
                if "pnl_pct" in with_result.columns:
                    equity = [100000.0]
                    for _, row in with_result.sort_values("timestamp").iterrows():
                        try:
                            pnl = float(row.get("pnl_pct", 0)) / 100
                            equity.append(equity[-1] * (1 + pnl))
                        except Exception:
                            pass

                    if len(equity) > 1:
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(
                            y=equity, mode="lines", name="Equity",
                            line=dict(color="#4CAF50", width=2),
                            fill="tozeroy", fillcolor="rgba(76,175,80,0.1)",
                        ))
                        fig.update_layout(
                            title="Curva de Equity", yaxis_title="Capital (EUR)",
                            height=300, paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                            font=dict(color="white"),
                        )
                        st.plotly_chart(fig)

        display = ["timestamp","symbol","direction","entry","sl","tp1","rr","confidence","lot_size","outcome"]
        cols    = [c for c in display if c in all_df.columns]
        st.dataframe(all_df[cols].head(200), hide_index=True)

        csv = all_df.to_csv(index=False)
        st.download_button(
            "⬇️ Exportar CSV", data=csv,
            file_name=f"signals_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )

# ── TAB 5: Backtest ──────────────────────────────────────────
with tab5:
    st.subheader("Resultados del Backtest")

    bt_data   = load_backtest_metrics()
    bt_equity = load_backtest_equity()
    bt_trades = load_backtest_trades()

    if bt_data is None:
        st.info(
            "Aun no hay resultados de backtest.\n\n"
            "**Como ejecutarlo:**\n"
            "1. Asegurate de que MetaTrader 5 este abierto y conectado\n"
            "2. Abre una terminal en `C:\\\\Users\\\\geost\\\\Desktop\\\\trading-system\\\\`\n"
            "3. Ejecuta: `python main.py --backtest`\n"
            "4. Espera ~2-3 minutos mientras se simulan las velas historicas\n"
            "5. Recarga el dashboard para ver los resultados aqui"
        )
    else:
        meta    = bt_data.get("meta", {})
        metrics = bt_data.get("metrics", {})

        st.caption(
            f"**{meta.get('symbol','')} {meta.get('timeframe','')}**  |  "
            f"{meta.get('date_from','')} → {meta.get('date_to','')}  |  "
            f"Generado: {meta.get('generated','')[:16]}"
        )

        # Fila de metricas clave
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        win_rate = metrics.get("win_rate", 0)
        pf       = metrics.get("profit_factor", 0)
        mdd      = metrics.get("max_drawdown", 0)
        sharpe   = metrics.get("sharpe_ratio", 0)
        total    = metrics.get("total_trades", 0)
        ret_pct  = metrics.get("total_return_pct", 0)

        m1.metric("Win Rate",      f"{win_rate:.1%}",
                  delta="bueno" if win_rate >= 0.45 else "bajo")
        m2.metric("Profit Factor", f"{pf:.2f}",
                  delta="bueno" if pf >= 1.5 else "bajo")
        m3.metric("Max Drawdown",  f"{mdd:.1%}",
                  delta="ok" if mdd <= 0.15 else "alto", delta_color="inverse")
        m4.metric("Sharpe Ratio",  f"{sharpe:.2f}",
                  delta="bueno" if sharpe >= 1.0 else "bajo")
        m5.metric("Total Trades",  total)
        m6.metric("Retorno Total", f"{ret_pct:+.2f}%",
                  delta_color="normal")

        st.divider()

        # Curva de equity
        if len(bt_equity) > 1:
            fig = go.Figure()
            profitable  = bt_equity[-1] >= bt_equity[0]
            line_color  = "#4CAF50" if profitable else "#f44336"
            fill_color  = "rgba(76,175,80,0.1)" if profitable else "rgba(244,67,54,0.1)"
            fig.add_trace(go.Scatter(
                y=bt_equity, mode="lines", name="Equity",
                line=dict(color=line_color, width=2),
                fill="tozeroy", fillcolor=fill_color,
            ))
            fig.update_layout(
                title="Curva de Equity — Backtest",
                yaxis_title="Capital (EUR)",
                xaxis_title="Trades",
                height=320,
                paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                font=dict(color="white"), margin=dict(t=40, b=30),
            )
            st.plotly_chart(fig, use_container_width=True)

        # Detalle por trade
        if not bt_trades.empty:
            st.subheader("Trades simulados")
            display_cols = [c for c in
                ["entry_time","direction","entry","sl","tp1","rr","result","pnl_pct","bars_to_close","bias"]
                if c in bt_trades.columns]
            st.dataframe(bt_trades[display_cols], hide_index=True)

            csv_bt = bt_trades.to_csv(index=False)
            st.download_button(
                "⬇️ Exportar trades CSV", data=csv_bt,
                file_name=f"backtest_{meta.get('symbol','XAUUSD')}_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )

# ── TAB 6: Sistema de Vida ────────────────────────────────────
with tab6:
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

    if not SUPABASE_URL or not SUPABASE_KEY:
        st.info("Configura SUPABASE_URL y SUPABASE_KEY como secrets de Streamlit para ver los datos.")
        st.stop()

    try:
        from supabase import create_client as _sb_create
        _sb = _sb_create(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        st.error(f"Error conectando Supabase: {e}")
        st.stop()

    from datetime import date as _date
    _today = _date.today().isoformat()
    _month_start = _today[:8] + "01"

    # ── XP State ──
    try:
        _xp_r = _sb.table("xp_state").select("state_json").eq("id", 1).execute()
        _xp = _xp_r.data[0]["state_json"] if _xp_r.data else {}
    except Exception:
        _xp = {}

    _streaks  = _xp.get("streaks", {})
    _total_xp = _xp.get("total_xp", 0)
    _skills   = _xp.get("skills", {})
    _logros   = _xp.get("achievements_unlocked", [])

    # ── Datos del mes ──
    try:
        _gastos_r = _sb.table("gastos").select("categoria,importe").gte("fecha", _month_start).execute()
        _dep_r    = _sb.table("deporte").select("fecha,actividad,sensacion").gte("fecha", _month_start).order("fecha", desc=True).execute()
        _alim_r   = _sb.table("alimentacion").select("fecha,kcal,prot_g,agua_l").gte("fecha", _month_start).order("fecha", desc=True).execute()
        _hab_r    = _sb.table("habitos").select("*").gte("fecha", _month_start).execute()
    except Exception as e:
        st.error(f"Error cargando datos: {e}")
        st.stop()

    # ── Header ──
    st.subheader("⚔️ Perfil del Aventurero")

    # Nivel global basado en XP
    _LEVELS = [(0,"Principiante"),(500,"Aprendiz"),(1500,"Iniciado"),(3000,"Practicante"),
               (6000,"Competente"),(10000,"Experto"),(15000,"Maestro"),(22000,"Gran Maestro"),
               (30000,"Leyenda"),(40000,"⭐ Inmortal")]
    _nivel = max((n for n, _ in _LEVELS if _total_xp >= n), default=0)
    _nivel_nombre = next((name for n, name in _LEVELS if n == _nivel), "Principiante")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🌟 Nivel Global", _nivel_nombre)
    c2.metric("✨ XP Total", f"{_total_xp:,}")
    c3.metric("🏆 Logros", f"{len(_logros)}/19")
    c4.metric("📅 Mes", _today[:7])

    st.divider()

    # ── Streaks ──
    st.subheader("🔥 Rachas actuales")
    s1, s2, s3 = st.columns(3)
    s1.metric("🚿 Ducha fría", f"{_streaks.get('ducha_fria',{}).get('current',0)} días")
    s2.metric("🌿 Té de clavo", f"{_streaks.get('te_clavo',{}).get('current',0)} días")
    s3.metric("🙏 Oración", f"{_streaks.get('oracion',{}).get('current',0)} días")

    # ── Hábitos del mes (barras) ──
    if _hab_r.data:
        _hab_df = pd.DataFrame(_hab_r.data)
        _hab_counts = {
            "Ducha fría 🚿": int(_hab_df["ducha_fria"].sum()),
            "Té de clavo 🌿": int(_hab_df["te_clavo"].sum()),
            "Oración 🙏": int(_hab_df["oracion"].sum()),
            "Silencio 🕊️": int(_hab_df["silencio"].sum()),
        }
        _days_elapsed = (_date.today() - _date(int(_today[:4]), int(_today[5:7]), 1)).days + 1
        fig_hab = go.Figure(go.Bar(
            x=list(_hab_counts.keys()),
            y=list(_hab_counts.values()),
            marker_color=["#4CAF50","#66BB6A","#81C784","#A5D6A7"],
            text=[f"{v}/{_days_elapsed}" for v in _hab_counts.values()],
            textposition="outside",
        ))
        fig_hab.update_layout(
            title=f"Hábitos cumplidos — {_today[:7]}",
            yaxis=dict(range=[0, _days_elapsed + 2]),
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="white"), height=280, margin=dict(t=40,b=20),
        )
        st.plotly_chart(fig_hab, use_container_width=True)

    st.divider()

    # ── Nutrición (última semana) ──
    st.subheader("🍽️ Nutrición — últimos registros")
    if _alim_r.data:
        _alim_df = pd.DataFrame(_alim_r.data).head(7)
        _TARGET_PROT = 150
        _TARGET_KCAL = 2800
        fig_nut = go.Figure()
        if "prot_g" in _alim_df.columns:
            fig_nut.add_trace(go.Bar(name="Proteína (g)", x=_alim_df["fecha"], y=_alim_df["prot_g"], marker_color="#4CAF50"))
            fig_nut.add_hline(y=_TARGET_PROT, line_dash="dash", line_color="#ff9800", annotation_text=f"Target {_TARGET_PROT}g")
        fig_nut.update_layout(
            title="Proteína diaria vs target (150g)",
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="white"), height=260, margin=dict(t=40,b=20),
        )
        st.plotly_chart(fig_nut, use_container_width=True)
    else:
        st.caption("Sin datos de nutrición este mes aún.")

    st.divider()

    # ── Gastos (pie chart) ──
    st.subheader("💶 Gastos del mes")
    if _gastos_r.data:
        _gdf = pd.DataFrame(_gastos_r.data)
        _por_cat = _gdf.groupby("categoria")["importe"].sum().reset_index()
        _total_g = _gdf["importe"].sum()
        fig_g = go.Figure(go.Pie(
            labels=_por_cat["categoria"], values=_por_cat["importe"],
            hole=0.4, textinfo="label+percent",
        ))
        fig_g.update_layout(
            title=f"Total: €{_total_g:.2f}",
            paper_bgcolor="#0e1117", font=dict(color="white"),
            height=300, margin=dict(t=40,b=10),
        )
        st.plotly_chart(fig_g, use_container_width=True)
    else:
        st.caption("Sin gastos registrados este mes aún.")

st.divider()
st.caption("Este sistema es una herramienta de analisis. No garantiza ganancias. Opera siempre con gestion de riesgo.")

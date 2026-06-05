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

tab1, tab_macro, tab2, tab3, tab4, tab5, tab6, tab_lectura, tab7 = st.tabs([
    "📈 Analisis de Mercado",
    "🌍 Macro & DXY",
    "🎯 Señales Activas",
    "📰 Noticias",
    "📊 Historial",
    "🧪 Backtest",
    "⚔️ Sistema de Vida",
    "📚 Lectura",
    "👨‍👩‍👧 Gastos Familia",
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


# ── TAB MACRO & DXY ──────────────────────────────────────────
with tab_macro:
    st.subheader("🌍 Macro & DXY — Contexto para XAUUSD")

    # Datos macro del market_state.json (generados cada ciclo)
    macro_state = state.get("macro", {})

    # ── Métricas rápidas ─────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    macro_details = macro_state.get("details", {})
    dxy_d  = macro_details.get("dxy",       {})
    tnx_d  = macro_details.get("bonds_10y", {})
    vix_d  = macro_details.get("vix",       {})
    spx_d  = macro_details.get("spx",       {})

    dxy_val = dxy_d.get("valor", "--")
    dxy_chg = dxy_d.get("cambio_6h", 0)
    dxy_impact = "⬇️ Positivo para ORO" if float(dxy_chg or 0) < 0 else "⬆️ Negativo para ORO"
    m1.metric("💵 DXY", f"{dxy_val}", f"{float(dxy_chg or 0):+.3f}% 6h — {dxy_impact}")

    tnx_val = tnx_d.get("valor", "--")
    tnx_chg = tnx_d.get("cambio_6h", 0)
    m2.metric("📉 Yields 10Y", f"{tnx_val}%", f"{float(tnx_chg or 0):+.4f} — {'↓ Bullish Gold' if float(tnx_chg or 0) < 0 else '↑ Bearish Gold'}")

    vix_now = vix_d.get("valor", "--")
    vix_avg = vix_d.get("media_20", "--")
    m3.metric("😱 VIX", f"{vix_now}", f"Media 20: {vix_avg} — {vix_d.get('mood','NEUTRAL')}")

    gold_bias = macro_state.get("gold_bias", "NEUTRAL")
    bias_color = "🟢" if gold_bias == "BULLISH" else ("🔴" if gold_bias == "BEARISH" else "⚪")
    m4.metric("🥇 Sesgo Macro Oro", f"{bias_color} {gold_bias}", f"Score: {macro_state.get('score', 0):+.2f}")

    st.divider()

    # ── Gráfico DXY M5 (últimas 8h vía yfinance) ────────────
    st.subheader("📈 DXY — US Dollar Index (M5, últimas 8h)")
    st.caption("Fuente: ^DXY (ICE) via yfinance — DXY ↓ = ORO ↑  |  DXY ↑ = ORO ↓")

    @st.cache_data(ttl=300)  # Cache 5 minutos
    def _load_dxy_chart():
        try:
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from data.macro_feed import MacroFeed
            mf = MacroFeed()
            return mf.get_dxy_chart_data(interval="5m", period="1d")
        except Exception:
            return pd.DataFrame()

    dxy_df = _load_dxy_chart()

    if not dxy_df.empty and "close" in dxy_df.columns:
        time_col = next((c for c in ["time", "Datetime", "Date", "date"] if c in dxy_df.columns), dxy_df.columns[0])
        fig_dxy  = go.Figure()

        # Velas (candlestick) si hay OHLC
        if all(c in dxy_df.columns for c in ["open", "high", "low", "close"]):
            fig_dxy.add_trace(go.Candlestick(
                x=dxy_df[time_col],
                open=dxy_df["open"], high=dxy_df["high"],
                low=dxy_df["low"],   close=dxy_df["close"],
                name="DXY",
                increasing_line_color="#f44336",   # rojo = DXY sube = oro baja
                decreasing_line_color="#4CAF50",   # verde = DXY baja = oro sube
            ))
        else:
            fig_dxy.add_trace(go.Scatter(
                x=dxy_df[time_col], y=dxy_df["close"],
                mode="lines", name="DXY", line=dict(color="#2196F3", width=2),
            ))

        # EMA20
        if "ema20" in dxy_df.columns:
            fig_dxy.add_trace(go.Scatter(
                x=dxy_df[time_col], y=dxy_df["ema20"],
                mode="lines", name="EMA20",
                line=dict(color="#ff9800", width=1.5, dash="dot"),
            ))

        # Anotación de impacto en el eje derecho
        last_close = float(dxy_df["close"].iloc[-1])
        fig_dxy.add_annotation(
            x=dxy_df[time_col].iloc[-1], y=last_close,
            text=f"  {last_close:.3f}",
            showarrow=False, font=dict(color="white", size=12),
        )

        fig_dxy.update_layout(
            height=380,
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="white"),
            xaxis_rangeslider_visible=False,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(t=30, b=30, l=10, r=10),
            yaxis_title="DXY",
        )
        st.plotly_chart(fig_dxy, use_container_width=True)
        st.caption("🔴 Velas rojas = DXY subiendo (presión bajista sobre ORO)  |  🟢 Velas verdes = DXY bajando (impulso alcista para ORO)")
    else:
        st.warning("No se pudo cargar el gráfico DXY. Verifica la conexión a internet.")

    st.divider()

    # ── Régimen de mercado ─────────────────────────────────────
    regime_state = state.get("symbols", {}).get("XAUUSD", {})
    st.subheader("📊 Régimen de Mercado — XAUUSD")

    rc1, rc2, rc3 = st.columns(3)
    rc1.metric("Sesgo Macro",    gold_bias)
    rc2.metric("DXY Tendencia",  dxy_d.get("ema_trend", "--"))
    rc3.metric("Modo Risk",      vix_d.get("mood", "NEUTRAL"))

    # Tabla de relaciones clave
    st.markdown("""
| Indicador | Valor actual | Impacto en ORO |
|-----------|-------------|----------------|
| **DXY ↑** | DXY sube | ❌ Bearish (correlación -0.85) |
| **DXY ↓** | DXY baja | ✅ Bullish |
| **Yields ↑** | Bonos suben | ❌ Bearish (coste oportunidad) |
| **VIX ↑** | Miedo mercado | ✅ Bullish (activo refugio) |
| **SPX ↓** | Bolsa cae | ✅ Bullish (flight to safety) |
""")

    # Detalles macro expandibles
    with st.expander("📋 Ver datos macro detallados"):
        if macro_details:
            st.json(macro_details)
        else:
            st.info("El sistema necesita estar corriendo (INICIAR.bat) para generar datos macro.")


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

    # ── Perfil nutricional ──
    try:
        _perfil_r = _sb.table("perfil_usuario").select("*").eq("id", 1).execute()
        _perfil = _perfil_r.data[0] if _perfil_r.data else {}
    except Exception:
        _perfil = {}

    _TARGET_KCAL = float(_perfil.get("target_kcal") or 2800)
    _TARGET_PROT = float(_perfil.get("target_prot") or 150)
    _TARGET_CARBS = float(_perfil.get("target_carbs") or 350)
    _TARGET_GRASAS = float(_perfil.get("target_grasas") or 78)

    if _perfil.get("peso"):
        st.subheader("🎯 Perfil nutricional")
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("⚖️ Peso", f"{_perfil.get('peso',0)} kg")
        p2.metric("📏 Altura", f"{_perfil.get('altura',0)} cm")
        p3.metric("💪 Tipo", str(_perfil.get("tipo_cuerpo","—")).capitalize())
        p4.metric("🔥 TDEE", f"{_perfil.get('tdee',0):.0f} kcal")
        st.divider()

    # ── Progreso de macros de hoy ──
    try:
        _hoy_alim = _sb.table("alimentacion").select("kcal,prot_g,carbs_g,grasas_g,agua_l").eq("fecha", _today).execute()
        _hoy_data = {"kcal": 0.0, "prot_g": 0.0, "carbs_g": 0.0, "grasas_g": 0.0, "agua_l": 0.0}
        for _row in (_hoy_alim.data or []):
            for _k in _hoy_data:
                _hoy_data[_k] += float(_row.get(_k) or 0)
    except Exception:
        _hoy_data = {"kcal": 0.0, "prot_g": 0.0, "carbs_g": 0.0, "grasas_g": 0.0, "agua_l": 0.0}

    st.subheader(f"📊 Macros de hoy — {_today}")
    _mc1, _mc2, _mc3, _mc4 = st.columns(4)
    _mc1.metric("🔥 Kcal", f"{_hoy_data['kcal']:.0f}", f"/{_TARGET_KCAL:.0f} target")
    _mc2.metric("💪 Proteína", f"{_hoy_data['prot_g']:.0f}g", f"/{_TARGET_PROT:.0f}g target")
    _mc3.metric("🌾 Carbos", f"{_hoy_data['carbs_g']:.0f}g", f"/{_TARGET_CARBS:.0f}g target")
    _mc4.metric("🥑 Grasas", f"{_hoy_data['grasas_g']:.0f}g", f"/{_TARGET_GRASAS:.0f}g target")

    _macro_names = ["🔥 Kcal", "💪 Proteína (g)", "🌾 Carbos (g)", "🥑 Grasas (g)"]
    _macro_actual = [_hoy_data["kcal"], _hoy_data["prot_g"], _hoy_data["carbs_g"], _hoy_data["grasas_g"]]
    _macro_target = [_TARGET_KCAL, _TARGET_PROT, _TARGET_CARBS, _TARGET_GRASAS]
    _macro_pct = [min(a / t * 100, 100) if t else 0 for a, t in zip(_macro_actual, _macro_target)]

    fig_macros = go.Figure()
    fig_macros.add_trace(go.Bar(
        name="Consumido", x=_macro_names, y=_macro_actual,
        marker_color=["#4CAF50" if p >= 90 else "#ff9800" if p >= 60 else "#f44336" for p in _macro_pct],
        text=[f"{p:.0f}%" for p in _macro_pct], textposition="outside",
    ))
    fig_macros.add_trace(go.Bar(
        name="Target", x=_macro_names, y=_macro_target,
        marker_color="rgba(255,255,255,0.15)",
    ))
    fig_macros.update_layout(
        barmode="overlay", title="Progreso macros de hoy vs targets personalizados",
        paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
        font=dict(color="white"), height=280, margin=dict(t=40, b=20),
    )
    st.plotly_chart(fig_macros, use_container_width=True)

    # ── Nutrición última semana ──
    st.subheader("🍽️ Nutrición — últimos 7 registros")
    if _alim_r.data:
        _alim_df = pd.DataFrame(_alim_r.data).head(7)
        fig_nut = go.Figure()
        if "prot_g" in _alim_df.columns:
            fig_nut.add_trace(go.Bar(name="Proteína (g)", x=_alim_df["fecha"], y=_alim_df["prot_g"], marker_color="#4CAF50"))
            fig_nut.add_hline(y=_TARGET_PROT, line_dash="dash", line_color="#ff9800", annotation_text=f"Target {_TARGET_PROT:.0f}g")
        if "kcal" in _alim_df.columns:
            fig_nut.add_trace(go.Scatter(name="Kcal/10", x=_alim_df["fecha"], y=_alim_df["kcal"] / 10, mode="lines+markers", line=dict(color="#2196F3")))
        fig_nut.update_layout(
            title="Proteína diaria vs target (últimos 7 días)",
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="white"), height=260, margin=dict(t=40, b=20),
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

# ── TAB LECTURA ───────────────────────────────────────────────
with tab_lectura:
    st.subheader("📚 Biblioteca Personal")

    _lec_url = os.environ.get("SUPABASE_URL", "")
    _lec_key = os.environ.get("SUPABASE_KEY", "")

    if not _lec_url or not _lec_key:
        st.info("Configura SUPABASE_URL y SUPABASE_KEY como secrets de Streamlit para ver la biblioteca.")
        st.stop()

    try:
        from supabase import create_client as _sblec_create
        _sblec = _sblec_create(_lec_url, _lec_key)
    except Exception as _elec:
        st.error(f"Error conectando Supabase: {_elec}")
        st.stop()

    try:
        _libros_r = _sblec.table("libros").select("*").execute()
        _libros_data = _libros_r.data or []
    except Exception as _elec2:
        st.error(f"Error cargando libros: {_elec2}")
        _libros_data = []

    if not _libros_data:
        st.info("La biblioteca esta vacia. Ejecuta `resources/populate_libros.py` despues de que termine la descarga.")
        st.stop()

    _df_lib = pd.DataFrame(_libros_data)
    _today_lec = datetime.now().strftime("%Y-%m-%d")

    # Metricas
    _total_lib   = len(_df_lib)
    _leidos_lib  = int((_df_lib["estado"] == "leido").sum())
    _leyendo_lib = int((_df_lib["estado"] == "leyendo").sum())
    _cats_leidas = int(_df_lib[_df_lib["estado"] == "leido"]["categoria"].nunique()) if _leidos_lib else 0

    lc1, lc2, lc3, lc4 = st.columns(4)
    lc1.metric("Biblioteca", f"{_total_lib:,} libros")
    lc2.metric("Leidos", _leidos_lib)
    lc3.metric("Leyendo ahora", _leyendo_lib)
    lc4.metric("Categorias exploradas", _cats_leidas)

    st.divider()

    # Libro actual
    _leyendo_df = _df_lib[_df_lib["estado"] == "leyendo"]
    if len(_leyendo_df) > 0:
        _libro_actual = _leyendo_df.iloc[0]
        st.subheader("Leyendo ahora")
        _lca, _lcb = st.columns([3, 1])
        with _lca:
            st.markdown(f"**{_libro_actual['titulo']}**")
            st.caption(f"{_libro_actual.get('autor') or '—'} · {_libro_actual.get('categoria') or '—'}")
            if _libro_actual.get("drive_url"):
                st.link_button("Abrir en Google Drive", _libro_actual["drive_url"])
            elif _libro_actual.get("ruta_archivo"):
                st.caption(f"`{_libro_actual['ruta_archivo']}`")
                if st.button("Abrir PDF (local)", key="lec_abrir_actual"):
                    try:
                        os.startfile(_libro_actual["ruta_archivo"])
                    except Exception as _eopen:
                        st.error(f"No se pudo abrir: {_eopen}")
        with _lcb:
            _rating_val = st.slider("Rating", 1, 5, int(_libro_actual.get("rating") or 3), key="lec_rating")
            _notas_val  = st.text_area("Notas", value=_libro_actual.get("notas") or "", height=80, key="lec_notas")
            if st.button("Marcar como leido", key="lec_btn_leido"):
                try:
                    _sblec.table("libros").update({
                        "estado": "leido", "rating": _rating_val,
                        "fecha_fin": _today_lec, "notas": _notas_val,
                    }).eq("id", int(_libro_actual["id"])).execute()
                    st.success("Libro marcado como leido!")
                    st.rerun()
                except Exception as _eu:
                    st.error(f"Error: {_eu}")
        st.divider()

    # Recomendacion semanal
    st.subheader("Recomendacion semanal")
    _cats_disp = sorted(_df_lib[_df_lib["estado"] == "pendiente"]["categoria"].dropna().unique().tolist())

    if _cats_disp:
        _rca, _rcb = st.columns([3, 1])
        with _rca:
            _cat_sel = st.selectbox("Categoria", _cats_disp, key="lec_cat")
        with _rcb:
            st.write("")
            st.write("")
            _btn_recom = st.button("Dame una recomendacion", key="lec_btn_recom")

        if _btn_recom:
            _pendientes_cat = _df_lib[(_df_lib["estado"] == "pendiente") & (_df_lib["categoria"] == _cat_sel)]
            if len(_pendientes_cat) > 0:
                _rec = _pendientes_cat.sample(1).iloc[0]
                st.session_state["lec_recom"] = _rec.to_dict()
            else:
                st.warning(f"No hay libros pendientes en '{_cat_sel}'")

        if "lec_recom" in st.session_state:
            _rec = st.session_state["lec_recom"]
            st.markdown(f"""
<div style="background:#1e2130;border-radius:8px;padding:16px;border-left:4px solid #4CAF50;margin:8px 0">
<h4 style="color:#4CAF50;margin:0 0 6px 0">{_rec.get('titulo','—')}</h4>
<p style="color:#ccc;margin:2px 0"><b>Autor:</b> {_rec.get('autor','—')}</p>
<p style="color:#ccc;margin:2px 0"><b>Categoria:</b> {_rec.get('categoria','—')}</p>
</div>""", unsafe_allow_html=True)
            if _rec.get("drive_url"):
                st.link_button("Abrir en Google Drive", _rec["drive_url"])
            elif _rec.get("ruta_archivo"):
                if st.button("Abrir PDF (local)", key="lec_btn_abrir_rec"):
                    try:
                        os.startfile(_rec["ruta_archivo"])
                    except Exception as _eo:
                        st.error(f"No se pudo abrir: {_eo}")
            if st.button("Empezar a leer este libro", key="lec_btn_empezar"):
                try:
                    _sblec.table("libros").update({
                        "estado": "leyendo", "fecha_inicio": _today_lec,
                    }).eq("id", int(_rec["id"])).execute()
                    del st.session_state["lec_recom"]
                    st.success(f"Empezando '{_rec['titulo']}'!")
                    st.rerun()
                except Exception as _es:
                    st.error(f"Error: {_es}")
    else:
        st.info("No hay libros pendientes. Has leido toda la biblioteca!")

    st.divider()

    # Estadisticas
    if _leidos_lib > 0:
        st.subheader("Progreso de lectura")
        _por_cat = (
            _df_lib[_df_lib["estado"] == "leido"]
            .groupby("categoria").size().reset_index(name="leidos")
            .sort_values("leidos", ascending=True)
        )
        if len(_por_cat) > 0:
            _fig_cat = go.Figure(go.Bar(
                x=_por_cat["leidos"], y=_por_cat["categoria"],
                orientation="h", marker_color="#4CAF50",
                text=_por_cat["leidos"], textposition="outside",
            ))
            _fig_cat.update_layout(
                title="Libros leidos por categoria",
                paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                font=dict(color="white"),
                height=max(200, len(_por_cat) * 30),
                margin=dict(t=40, b=10, l=10, r=40),
            )
            st.plotly_chart(_fig_cat, use_container_width=True)

        _ultimos_lib = (
            _df_lib[_df_lib["estado"] == "leido"]
            .sort_values("fecha_fin", ascending=False).head(5)
        )
        st.caption("Ultimos 5 leidos:")
        _cols_ult = [c for c in ["titulo","autor","categoria","rating","fecha_fin"] if c in _ultimos_lib.columns]
        st.dataframe(
            _ultimos_lib[_cols_ult].rename(columns={
                "titulo":"Titulo","autor":"Autor","categoria":"Categoria",
                "rating":"Rating","fecha_fin":"Fecha fin",
            }),
            hide_index=True, use_container_width=True,
        )
        st.divider()

    # Biblioteca completa
    st.subheader("Biblioteca completa")
    _filtro_cats = st.multiselect(
        "Filtrar por categoria",
        sorted(_df_lib["categoria"].dropna().unique().tolist()),
        key="lec_filtro_cats",
    )
    _filtro_estado = st.radio(
        "Estado", ["Todos","Pendiente","Leyendo","Leido"], horizontal=True, key="lec_filtro_estado"
    )
    _df_filtrado = _df_lib.copy()
    if _filtro_cats:
        _df_filtrado = _df_filtrado[_df_filtrado["categoria"].isin(_filtro_cats)]
    if _filtro_estado != "Todos":
        _em = {"Pendiente":"pendiente","Leyendo":"leyendo","Leido":"leido"}
        _df_filtrado = _df_filtrado[_df_filtrado["estado"] == _em[_filtro_estado]]
    st.caption(f"Mostrando {len(_df_filtrado):,} libros")
    _df_mostrar = _df_filtrado.copy()
    # Columna Abrir: link de Drive si existe, vacío si no
    _df_mostrar["Abrir"] = _df_mostrar["drive_url"].fillna("") if "drive_url" in _df_mostrar.columns else ""
    _cols_show = [c for c in ["titulo","autor","categoria","estado","rating","fecha_fin"] if c in _df_mostrar.columns]
    st.dataframe(
        _df_mostrar[_cols_show + ["Abrir"]].rename(columns={
            "titulo":"Titulo","autor":"Autor","categoria":"Categoria",
            "estado":"Estado","rating":"Rating","fecha_fin":"Terminado",
        }),
        column_config={
            "Abrir": st.column_config.LinkColumn("Abrir", display_text="Abrir libro"),
        },
        hide_index=True, use_container_width=True,
    )


# ── TAB 7: Gastos Familia ─────────────────────────────────────
with tab7:
    _sb_url = os.environ.get("SUPABASE_URL", "")
    _sb_key = os.environ.get("SUPABASE_KEY", "")

    if not _sb_url or not _sb_key:
        st.info("Configura SUPABASE_URL y SUPABASE_KEY para ver los gastos familiares.")
        st.stop()

    try:
        from supabase import create_client as _sb7_create
        _sb7 = _sb7_create(_sb_url, _sb_key)
    except Exception as _e7:
        st.error(f"Error conectando Supabase: {_e7}")
        st.stop()

    from datetime import date as _date7
    _today7 = _date7.today().isoformat()
    _month7 = _today7[:8] + "01"

    st.subheader("👨‍👩‍👧 Gastos Familiares — Supermercado")

    # ── Datos del mes ──
    try:
        _gf_r = _sb7.table("gastos_familia").select("*").gte("fecha", _month7).order("fecha", desc=True).execute()
        _ic_r = _sb7.table("items_compra").select("item_nombre,total").gte("fecha", _month7).execute()
    except Exception as _e7b:
        st.error(f"Error cargando datos: {_e7b}")
        st.stop()

    _gf_data = _gf_r.data or []
    _ic_data = _ic_r.data or []

    # ── Métricas ──
    _total_mes = sum(float(r.get("importe", 0)) for r in _gf_data)
    _n_compras = len([r for r in _gf_data if r.get("origen") == "foto"])
    _n_registros = len(_gf_data)

    _fm1, _fm2, _fm3 = st.columns(3)
    _fm1.metric("💶 Total gastado este mes", f"€{_total_mes:.2f}")
    _fm2.metric("🧾 Tickets escaneados", _n_compras)
    _fm3.metric("📝 Registros totales", _n_registros)

    st.divider()

    if _gf_data:
        _gf_df = pd.DataFrame(_gf_data)

        col_izq, col_der = st.columns(2)

        # ── Gráfico: Desglose por categoría ──
        with col_izq:
            st.subheader("📊 Gasto por categoría")
            _por_cat = _gf_df.groupby("categoria")["importe"].sum().reset_index()
            fig_cat = go.Figure(go.Pie(
                labels=_por_cat["categoria"],
                values=_por_cat["importe"],
                hole=0.4,
                textinfo="label+percent+value",
                texttemplate="%{label}<br>%{percent}<br>€%{value:.2f}",
            ))
            fig_cat.update_layout(
                title=f"Total: €{_total_mes:.2f}",
                paper_bgcolor="#0e1117", font=dict(color="white"),
                height=350, margin=dict(t=40, b=10),
            )
            st.plotly_chart(fig_cat, use_container_width=True)

        # ── Gráfico: Top items comprados ──
        with col_der:
            st.subheader("🛒 Top items más comprados")
            if _ic_data:
                _ic_df = pd.DataFrame(_ic_data)
                _ic_df["item_nombre"] = _ic_df["item_nombre"].str.strip().str.title()
                _top_items = (
                    _ic_df.groupby("item_nombre")["total"]
                    .sum()
                    .reset_index()
                    .sort_values("total", ascending=True)
                    .tail(10)
                )
                fig_items = go.Figure(go.Bar(
                    x=_top_items["total"],
                    y=_top_items["item_nombre"],
                    orientation="h",
                    marker_color="#2196F3",
                    text=[f"€{v:.2f}" for v in _top_items["total"]],
                    textposition="outside",
                ))
                fig_items.update_layout(
                    title="Top 10 por gasto total (€)",
                    paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                    font=dict(color="white"), height=350,
                    margin=dict(t=40, b=10, l=10, r=60),
                    xaxis_title="€ gastados",
                )
                st.plotly_chart(fig_items, use_container_width=True)
            else:
                st.caption("Aún no hay items de tickets escaneados este mes.")

        st.divider()

        # ── Evolución diaria del gasto ──
        st.subheader("📈 Gasto acumulado por día")
        _gf_df["fecha"] = pd.to_datetime(_gf_df["fecha"])
        _daily = _gf_df.groupby("fecha")["importe"].sum().reset_index()
        _daily["acumulado"] = _daily["importe"].cumsum()
        fig_evo = go.Figure()
        fig_evo.add_trace(go.Bar(name="Gasto del día", x=_daily["fecha"], y=_daily["importe"], marker_color="#ff9800"))
        fig_evo.add_trace(go.Scatter(name="Acumulado", x=_daily["fecha"], y=_daily["acumulado"], mode="lines+markers", line=dict(color="#4CAF50", width=2)))
        fig_evo.update_layout(
            paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
            font=dict(color="white"), height=280, margin=dict(t=20, b=20),
        )
        st.plotly_chart(fig_evo, use_container_width=True)

        st.divider()

        # ── Historial de compras ──
        st.subheader("📋 Historial de compras")
        _hist_cols = [c for c in ["fecha", "miembro", "concepto", "importe", "categoria", "origen"] if c in _gf_df.columns]
        _gf_df["fecha"] = _gf_df["fecha"].dt.strftime("%Y-%m-%d")
        st.dataframe(_gf_df[_hist_cols].rename(columns={"fecha": "Fecha", "miembro": "Quién", "concepto": "Qué", "importe": "€", "categoria": "Categoría", "origen": "Tipo"}), hide_index=True, use_container_width=True)
    else:
        st.info("No hay gastos familiares registrados este mes. Añade el bot al grupo de Telegram y empieza a registrar compras.")

"""
Daytrade Backtester — Valida el stream DAYTRADE M15 sobre histórico MT5.

Replica la lógica de SignalEngine.analyze_daytrade():
  - Bias H1 (EMA 20/50/200; fallback estructura) — ambas direcciones
  - Order Block M15 con entrada LIMIT en retroceso
  - SL = extremo OB M15 ± atr_sl_multiplier × ATR M15 | TP1 = min_rr × R
  - Vetos de liquidez sobre M15: sweep-contra (niveles significativos),
    displacement-contra y pool HTF (H1/H4) intacto
  - RSI M15 extremo contra → descarte | filtro de sesión 7-21 UTC
  - Anti-duplicado por contexto (mismo OB / entry < 0.5 ATR M15)
  - Resolución como el OutcomeTracker en vivo: fill en 16 velas o EXPIRED,
    SL/TP1 en 96 velas o EXPIRED (sin gestión — ejecución manual a TP1 fijo)

Costes incluidos: spread fijo (default $0.35 XAUUSD) + comisión $7/lote,
ambos convertidos a R sobre la distancia del SL (en SL cortos pesan mucho).

Confluencias computables offline: bias H1 (1.5) + H4 alineado (1.0) +
estructura M15 (1.0) + RSI (0.5/0.25) + sin-noticias asumido (0.5) +
sweep a favor (1.0) + FVG (0.5) = máx 6.25. VP COMEX y Delta no existen
en histórico → el gate del backtest es algo MÁS estricto que el vivo.

Ejecutar:
    python backtest/daytrade_backtester.py            → último año
    python backtest/daytrade_backtester.py --days 180 → últimos 6 meses
"""

import sys
import os
import json
import logging
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
LOGS_DIR = os.path.join(_PROJECT_ROOT, "logs")

from analysis.indicators import add_indicators, get_ema_bias
from analysis.market_structure import (
    find_order_blocks,
    find_fair_value_gaps,
    detect_market_structure,
    detect_liquidity_sweep,
    detect_displacement,
    get_significant_levels,
    find_htf_liquidity_pools,
)

logger = logging.getLogger(__name__)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _risk_usd_per_lot(symbol: str, risk_price: float, config: dict) -> float:
    """Moneda de cuenta que arriesga 1 lote para una distancia de SL en precio.
    Mismo criterio que risk_manager.calculate_lot_size — generaliza el backtester
    a forex/índices (antes asumía oro: risk*100, que rompía el coste en forex)."""
    sym_u     = symbol.upper()
    contracts = (config.get("symbols", {}) or {}).get("contracts", {}) or {}
    if any(x in sym_u for x in ("XAU", "GOLD")):
        return risk_price * 100.0
    if sym_u in contracts:
        c   = contracts[sym_u]
        pip = float(c.get("pip", 1.0)) or 1.0
        return (risk_price / pip) * float(c.get("value_per_lot", 1.0))
    if "JPY" in sym_u:
        return (risk_price / 0.01) * 9.0
    return (risk_price / 0.0001) * 10.0


def run_daytrade_backtest(mt5_connector, symbol: str,
                          date_from: datetime, date_to: datetime,
                          config: dict, spread_usd: float = 0.35,
                          hour_ranges: list = None,
                          require_h4: bool = False) -> dict:
    dt_cfg   = config.get("daytrade", {}) or {}
    liq_cfg  = config.get("liquidity", {}) or {}
    sess_cfg = config.get("sessions", {}).get("allowed_hours_utc", {})
    p_cfg    = config.get("personal", {}) or {}

    min_rr     = float(dt_cfg.get("min_rr", 1.5))
    atr_mult   = float(dt_cfg.get("atr_sl_multiplier", 1.0))
    min_conf   = float(dt_cfg.get("min_confluences", 4.0))
    fill_bars  = int(dt_cfg.get("expiry_bars_m15", 16))
    max_bars   = int(dt_cfg.get("max_outcome_bars_m15", 96))
    sess_start = int(sess_cfg.get("start", 7))
    sess_end   = int(sess_cfg.get("end", 21))
    comm_lot   = float(config.get("backtest", {}).get("commission_per_lot", 7.0))
    risk_pct   = float(p_cfg.get("risk_per_trade", 0.01))
    p_balance  = float(p_cfg.get("balance", 200))
    p_max_risk = float(p_cfg.get("max_risk_pct", 0.03))

    sweep_veto_on = bool(liq_cfg.get("sweep_against_veto", True))
    disp_veto_on  = bool(liq_cfg.get("displacement_filter", True))
    htf_veto_on   = bool(liq_cfg.get("htf_liquidity_veto", True))
    liq_lookback  = int(liq_cfg.get("sweep_against_lookback", 6))
    disp_candles  = int(liq_cfg.get("displacement_candles", 3))
    disp_body     = float(liq_cfg.get("displacement_body_atr", 0.8))
    disp_net      = float(liq_cfg.get("displacement_net_atr", 1.5))
    htf_lookback  = int(liq_cfg.get("htf_swing_lookback", 3))

    print("=" * 58)
    print(f"  BACKTEST DAYTRADE M15 — {symbol}")
    print(f"  {date_from:%Y-%m-%d} → {date_to:%Y-%m-%d}")
    print(f"  Umbral: {min_conf} confluencias | TP1 {min_rr}R fijo (manual)")
    print(f"  Fill: {fill_bars} velas | Horizonte: {max_bars} velas M15")
    print(f"  Costes: spread ${spread_usd:.2f} + comisión ${comm_lot:.0f}/lote")
    print("=" * 58)

    print("  Descargando datos históricos...")
    df_m15 = mt5_connector.get_rates_range(symbol, "M15", date_from, date_to)
    df_h1  = mt5_connector.get_rates_range(symbol, "H1",  date_from, date_to)
    df_h4  = mt5_connector.get_rates_range(symbol, "H4",  date_from, date_to)
    if df_m15.empty or df_h1.empty:
        print("  ERROR: Sin datos históricos M15/H1. ¿MT5 está abierto?")
        return {}

    # Indicadores precalculados UNA vez (EMA/ATR/RSI son recursivos hacia
    # adelante — el valor en la barra i no usa datos futuros)
    m15 = add_indicators(df_m15)
    h1  = add_indicators(df_h1)
    h4  = add_indicators(df_h4) if not df_h4.empty else pd.DataFrame()
    n   = len(m15)
    print(f"  Datos: {n:,} velas M15 | {len(h1):,} H1 | {len(h4):,} H4")

    h1_times = h1["time"].values
    h4_times = h4["time"].values if not h4.empty else np.array([])

    trades        = []
    equity        = [p_balance]
    last_sig      = None   # anti-duplicado por contexto
    discards      = {"session": 0, "bias": 0, "rsi": 0, "spread": 0,
                     "sweep": 0, "disp": 0, "htf": 0, "no_ob": 0,
                     "conf": 0, "dup": 0, "no_fill": 0}
    open_until    = -1     # no apilar: 1 trade simulado a la vez

    for i in range(300, n - 2):
        if i % 2000 == 0:
            pct = (i - 300) / max(1, n - 302) * 100
            print(f"  [{pct:5.1f}%]  Trades: {len(trades)}")

        if i <= open_until:
            continue

        t_i  = m15["time"].iloc[i]
        hour = t_i.hour if hasattr(t_i, "hour") else pd.Timestamp(t_i).hour
        if hour_ranges:
            # Solo ventanas concretas (ej. aperturas Londres + NY)
            if not any(lo <= hour < hi for lo, hi in hour_ranges):
                discards["session"] += 1
                continue
        elif not (sess_start <= hour < sess_end):
            discards["session"] += 1
            continue

        # ── Bias H1 hasta la barra actual ──────────────────────
        h1_idx = np.searchsorted(h1_times, np.datetime64(pd.Timestamp(t_i))) - 1
        if h1_idx < 60:
            continue
        h1_win = h1.iloc[max(0, h1_idx - 200):h1_idx + 1]
        bias   = get_ema_bias(h1_win)
        if bias == "NEUTRAL":
            struct_h1 = detect_market_structure(
                h1_win.tail(80).reset_index(drop=True), lookback=5)
            bias = struct_h1["trend"]
        if bias == "NEUTRAL":
            discards["bias"] += 1
            continue
        direction   = "BUY" if bias == "BULLISH" else "SELL"
        target_type = "BULLISH" if direction == "BUY" else "BEARISH"

        # ── Ventana M15 + métricas de la barra ─────────────────
        win = m15.iloc[max(0, i - 300):i + 1].reset_index(drop=True)
        price   = float(win["close"].iloc[-1])
        atr_m15 = float(win["atr"].iloc[-1])
        atr_h1  = float(h1_win["atr"].iloc[-1])
        rsi_val = float(win["rsi"].iloc[-1])
        if atr_m15 <= 0:
            continue
        if (direction == "BUY" and rsi_val > 70) or \
           (direction == "SELL" and rsi_val < 30):
            discards["rsi"] += 1
            continue

        # Spread como % del SL estimado (mismo filtro que en vivo)
        est_sl = atr_m15 * (atr_mult + 0.5)
        if est_sl > 0 and spread_usd > est_sl * 0.15:
            discards["spread"] += 1
            continue

        struct_m15 = detect_market_structure(win, lookback=5)

        # ── Vetos de liquidez ──────────────────────────────────
        if sweep_veto_on:
            opposite   = "SELL" if direction == "BUY" else "BUY"
            sig_levels = get_significant_levels(win)
            lvls = sig_levels["lows"] if opposite == "BUY" else sig_levels["highs"]
            if lvls:
                sw = detect_liquidity_sweep(
                    win, struct_m15["swing_highs"], struct_m15["swing_lows"],
                    opposite, lookback=liq_lookback, levels=lvls)
                if sw["swept"]:
                    discards["sweep"] += 1
                    continue

        if disp_veto_on:
            disp = detect_displacement(
                win, atr_m15, n_candles=disp_candles,
                body_atr=disp_body, net_atr=disp_net, exclude_last=True)
            if (disp["direction"] == "BULLISH" and direction == "SELL") or \
               (disp["direction"] == "BEARISH" and direction == "BUY"):
                discards["disp"] += 1
                continue

        h4_win = pd.DataFrame()
        if not h4.empty:
            h4_idx = np.searchsorted(h4_times, np.datetime64(pd.Timestamp(t_i))) - 1
            if h4_idx >= 30:
                h4_win = h4.iloc[max(0, h4_idx - 120):h4_idx + 1]

        # Variante: H4 alineado como requisito DURO (no solo confluencia)
        if require_h4:
            if h4_win.empty or get_ema_bias(h4_win) != bias:
                discards["bias"] += 1
                continue

        if htf_veto_on:
            pools = find_htf_liquidity_pools(
                [(h1_win.tail(120), "H1"), (h4_win, "H4")], price, atr_h1,
                max_dist_atr=1.0, swing_lookback=htf_lookback)
            in_path = pools["above"] if direction == "SELL" else pools["below"]
            if in_path:
                sw_own = detect_liquidity_sweep(
                    win, struct_m15["swing_highs"], struct_m15["swing_lows"],
                    direction, lookback=liq_lookback,
                    levels=[p["price"] for p in in_path])
                if not sw_own["swept"]:
                    discards["htf"] += 1
                    continue

        # ── Order Block M15 ────────────────────────────────────
        obs = find_order_blocks(win, n_recent=40)
        ob  = None
        for cand in obs:
            if not cand["valid"] or cand["type"] != target_type:
                continue
            if direction == "BUY":
                if 0 <= (price - cand["top"]) <= atr_m15 * 2 or \
                   cand["bottom"] <= price <= cand["top"]:
                    ob = cand; break
            else:
                if 0 <= (cand["bottom"] - price) <= atr_m15 * 2 or \
                   cand["bottom"] <= price <= cand["top"]:
                    ob = cand; break
        if ob is None:
            discards["no_ob"] += 1
            continue

        inside = ob["bottom"] <= price <= ob["top"]
        if direction == "BUY":
            entry = price if inside else float(ob["top"])
            sl    = float(ob["bottom"]) - atr_m15 * atr_mult
            risk  = entry - sl
        else:
            entry = price if inside else float(ob["bottom"])
            sl    = float(ob["top"]) + atr_m15 * atr_mult
            risk  = sl - entry
        if risk <= 0:
            continue
        tp1 = entry + risk * min_rr if direction == "BUY" else entry - risk * min_rr

        # ── Confluencias (subset computable offline) ───────────
        conf = 1.5  # bias H1
        if not h4_win.empty and get_ema_bias(h4_win) == bias:
            conf += 1.0
        ev = struct_m15.get("last_event")
        if struct_m15["trend"] == bias or (ev and ev["direction"] == bias):
            conf += 1.0
        if (direction == "BUY" and rsi_val < 40) or \
           (direction == "SELL" and rsi_val > 60):
            conf += 0.5
        elif 40 <= rsi_val <= 60:
            conf += 0.25
        conf += 0.5  # sin noticias (no hay calendario histórico)
        sw_fav = detect_liquidity_sweep(
            win, struct_m15["swing_highs"], struct_m15["swing_lows"], direction)
        if sw_fav["swept"]:
            conf += 1.0
        for gap in find_fair_value_gaps(win, n_recent=40):
            if gap["valid"] and gap["type"] == target_type and (
                    gap["bottom"] <= entry <= gap["top"] or
                    abs(entry - gap["midpoint"]) <= atr_m15 * 0.5):
                conf += 0.5
                break
        if conf < min_conf:
            discards["conf"] += 1
            continue

        # ── Anti-duplicado por contexto ────────────────────────
        if last_sig and last_sig["direction"] == direction:
            if abs(entry - last_sig["entry"]) < atr_m15 * 0.5 or \
               abs(float(ob["midpoint"]) - last_sig["ob_mid"]) < atr_m15 * 0.25:
                discards["dup"] += 1
                continue
        last_sig = {"direction": direction, "entry": entry,
                    "ob_mid": float(ob["midpoint"])}

        # ── Simulación: fill → SL/TP1 (como el OutcomeTracker) ─
        result, bars_used = _simulate(m15, i, direction, entry, sl, tp1,
                                      fill_bars, max_bars, market=inside)
        if result == "NO_FILL":
            discards["no_fill"] += 1
            continue
        open_until = i + bars_used

        # Costes en R: spread (precio) + comisión (por lote → R vía valor del lote)
        risk_acc_lot = _risk_usd_per_lot(symbol, risk, config)
        cost_r = (spread_usd / risk) + \
                 (comm_lot / risk_acc_lot if risk_acc_lot > 0 else 0.0)
        pnl_r  = (min_rr if result == "WIN" else
                  (-1.0 if result == "LOSS" else 0.0)) - \
                 (cost_r if result in ("WIN", "LOSS") else 0.0)

        # Equity de la cuenta personal: 1% por trade, suelo lote 0.01.
        # risk_acc_lot ya está en la moneda de la cuenta (EUR en este broker).
        eq        = equity[-1]
        lots      = max(0.01, np.floor((eq * risk_pct) / risk_acc_lot / 0.01) * 0.01) \
                    if risk_acc_lot > 0 else 0.01
        risk_eur  = lots * risk_acc_lot
        apta      = (risk_eur / eq) <= p_max_risk if eq > 0 else False
        pnl_eur   = pnl_r * risk_eur if apta else 0.0
        equity.append(eq + pnl_eur)

        trades.append({
            "time":      str(t_i), "direction": direction,
            "entry":     round(entry, 5), "sl": round(sl, 5),
            "tp1":       round(tp1, 5), "sl_dist": round(risk, 5),
            "result":    result, "pnl_r": round(pnl_r, 3),
            "bars":      bars_used, "conf": round(conf, 2),
            "apta_200":  bool(apta), "risk_eur": round(risk_eur, 2),
            "pnl_eur":   round(pnl_eur, 2),
        })

    return _report(trades, equity, discards, date_from, date_to,
                   p_balance, symbol, min_rr=min_rr)


def _simulate(m15, i, direction, entry, sl, tp1, fill_bars, max_bars,
              market=False):
    """Replica del OutcomeTracker: fase fill (LIMIT) + fase SL/TP1."""
    n      = len(m15)
    filled = market
    for k in range(i + 1, min(i + 1 + max_bars, n)):
        lo = float(m15["low"].iloc[k])
        hi = float(m15["high"].iloc[k])
        if not filled:
            if (k - i - 1) >= fill_bars:
                return "NO_FILL", k - i
            if direction == "BUY" and lo <= entry:
                filled = True
                if lo <= sl:           # conservador: SL primero en la vela del fill
                    return "LOSS", k - i
            elif direction == "SELL" and hi >= entry:
                filled = True
                if hi >= sl:
                    return "LOSS", k - i
            continue
        if direction == "BUY":
            if lo <= sl:
                return "LOSS", k - i
            if hi >= tp1:
                return "WIN", k - i
        else:
            if hi >= sl:
                return "LOSS", k - i
            if lo <= tp1:
                return "WIN", k - i
    return ("EXPIRED", max_bars) if filled else ("NO_FILL", max_bars)


def _report(trades, equity, discards, date_from, date_to, balance0, symbol,
            min_rr: float = 1.5):
    closed  = [t for t in trades if t["result"] in ("WIN", "LOSS")]
    wins    = [t for t in closed if t["result"] == "WIN"]
    losses  = [t for t in closed if t["result"] == "LOSS"]
    expired = [t for t in trades if t["result"] == "EXPIRED"]
    days    = max(1, (date_to - date_from).days)

    wr = len(wins) / len(closed) if closed else 0
    gp = sum(t["pnl_r"] for t in closed if t["pnl_r"] > 0)
    gl = abs(sum(t["pnl_r"] for t in closed if t["pnl_r"] < 0))
    pf = gp / gl if gl > 0 else float("inf")

    eq   = np.array(equity)
    peak = np.maximum.accumulate(eq)
    dd   = float(((peak - eq) / peak).max()) if len(eq) > 1 else 0.0

    sl_dists = [t["sl_dist"] for t in trades]
    aptas    = [t for t in trades if t["apta_200"]]

    metrics = {
        "symbol":        symbol,
        "model":         "DAYTRADE_M15",
        "period":        f"{date_from:%Y-%m-%d} → {date_to:%Y-%m-%d}",
        "signals":       len(trades),
        "closed":        len(closed),
        "wins":          len(wins),
        "losses":        len(losses),
        "expired":       len(expired),
        "win_rate":      round(wr, 4),
        "profit_factor": round(pf, 2) if pf != float("inf") else 99.0,
        "net_r":         round(sum(t["pnl_r"] for t in closed), 1),
        "max_drawdown":  round(dd, 4),
        "signals_week":  round(len(trades) / days * 7, 1),
        "sl_median_usd": round(float(np.median(sl_dists)), 2) if sl_dists else 0,
        "aptas_200eur":  len(aptas),
        "aptas_pct":     round(len(aptas) / len(trades), 3) if trades else 0,
        "equity_final":  round(float(eq[-1]), 2),
        "return_pct":    round((float(eq[-1]) / balance0 - 1) * 100, 1),
        "discards":      discards,
    }

    be_wr = 1.0 / (1.0 + min_rr)  # breakeven teórico con TP min_rr fijo
    print()
    print("=" * 58)
    print(f"  RESULTADOS DAYTRADE M15 — {symbol}")
    print("=" * 58)
    print(f"  Señales:        {len(trades)}  (~{metrics['signals_week']}/semana)")
    print(f"  Cerradas:       {len(closed)}  (WIN {len(wins)} / LOSS {len(losses)} / EXP {len(expired)})")
    print(f"  Win Rate:       {wr:.1%}   (breakeven {be_wr:.1%} con TP 1.5R)")
    print(f"  Profit Factor:  {metrics['profit_factor']}")
    print(f"  Neto:           {metrics['net_r']:+.1f} R")
    print(f"  Max Drawdown:   {dd:.1%}  (cuenta {balance0:.0f} EUR @1%)")
    print(f"  Equity final:   {metrics['equity_final']:.2f} EUR ({metrics['return_pct']:+.1f}%)")
    print(f"  SL mediano:     {metrics['sl_median_usd']}  | aptas cuenta 200€: {metrics['aptas_pct']:.0%}")
    print(f"  Descartes:      {discards}")
    print("=" * 58)
    ok = (len(closed) >= 40 and wr > be_wr and
          metrics["profit_factor"] > 1.0 and metrics["net_r"] > 0)
    if ok:
        print(f"  ✅ PASA el filtro base (≥40 trades, WR>{be_wr:.0%}, PF>1, neto+).")
        print(f"     Revalidar OOS (año anterior) antes de activar en vivo.")
    else:
        print(f"  ❌ NO pasa el filtro base — NO activar {symbol} en daytrade.")
    print("=" * 58)

    os.makedirs(LOGS_DIR, exist_ok=True)
    suffix = os.environ.get("DT_BT_SUFFIX", "")
    safe   = symbol.replace("/", "").replace(" ", "")
    out = os.path.join(LOGS_DIR, f"backtest_daytrade{suffix}_{safe}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"metrics": metrics, "trades": trades[-300:]}, f, indent=2)
    print(f"  Guardado: {out}")

    return {"metrics": metrics, "trades": trades, "equity_curve": equity}


if __name__ == "__main__":
    import yaml
    from data.mt5_connector import MT5Connector

    logging.basicConfig(level=logging.WARNING)
    with open(os.path.join(_PROJECT_ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    symbol = cfg["symbols"]["primary"]
    if "--symbol" in sys.argv:
        symbol = sys.argv[sys.argv.index("--symbol") + 1]

    days = 365
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    spread = 0.35
    if "--spread" in sys.argv:
        spread = float(sys.argv[sys.argv.index("--spread") + 1])

    # Overrides para barrer variantes: --rr 2.0 --conf 5.0 --atrmult 1.5
    cfg.setdefault("daytrade", {})
    if "--rr" in sys.argv:
        cfg["daytrade"]["min_rr"] = float(sys.argv[sys.argv.index("--rr") + 1])
    if "--conf" in sys.argv:
        cfg["daytrade"]["min_confluences"] = float(sys.argv[sys.argv.index("--conf") + 1])
    if "--atrmult" in sys.argv:
        cfg["daytrade"]["atr_sl_multiplier"] = float(sys.argv[sys.argv.index("--atrmult") + 1])
    # Ventanas horarias: --hours 7-10,13-16 manda; si no, config daytrade.hours_utc
    hour_ranges = None
    spec = ""
    if "--hours" in sys.argv:
        spec = sys.argv[sys.argv.index("--hours") + 1]
    elif cfg["daytrade"].get("hours_utc"):
        spec = str(cfg["daytrade"]["hours_utc"])
    if spec.strip():
        hour_ranges = [tuple(int(x) for x in r.split("-")) for r in spec.split(",")]
    require_h4 = "--requireh4" in sys.argv

    conn = MT5Connector(cfg)
    if not conn.connect():
        print("ERROR: MT5 no disponible. ¿Está abierto?")
        sys.exit(1)

    dt_to   = datetime.now(timezone.utc)
    dt_from = dt_to - timedelta(days=days)
    # Ventanas explícitas para validación IS/OOS: --from 2024-07-04 --to 2025-07-04
    if "--to" in sys.argv:
        dt_to = datetime.fromisoformat(
            sys.argv[sys.argv.index("--to") + 1]).replace(tzinfo=timezone.utc)
        dt_from = dt_to - timedelta(days=days)
    if "--from" in sys.argv:
        dt_from = datetime.fromisoformat(
            sys.argv[sys.argv.index("--from") + 1]).replace(tzinfo=timezone.utc)
    run_daytrade_backtest(conn, symbol, dt_from, dt_to,
                          cfg, spread_usd=spread,
                          hour_ranges=hour_ranges, require_h4=require_h4)
    conn.disconnect()

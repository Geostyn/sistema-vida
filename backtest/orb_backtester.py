"""
ORB Backtester — Opening Range Breakout para índices/forex (modelo MOMENTUM).

Por qué: el modelo de retroceso a Order Block (oro) es mean-reversion y PIERDE en
forex/índices (backtest 2026-06-23: EURUSD PF 0.73, US30 0.62, US500 0.31). Los
índices TENDENCIAN intradía → hace falta un modelo de ruptura, no de reversión.

Método (Zarattini & Aziz 2023, "Can Day Trading Really Be Profitable?", QQQ 5-min
ORB +1484% 2016-2023; consenso futuros ES/NQ):
  - Definir el RANGO DE APERTURA = high/low de los primeros `or_min` minutos de la
    sesión cash US (9:30 ET = 13:30 UTC = 16:30 hora del servidor MT5 = UTC+3).
  - Entrar al PRIMER cierre de vela fuera del rango (ruptura) — long arriba, short abajo.
  - Stop = lado opuesto del rango. Target = `rr` × riesgo (2R por defecto; el paper usa
    10R + salida al cierre). Si rr<=0: sin target, salir al cierre de sesión (estilo paper).
  - UNA operación al día. Time-stop: si no rompe en `timestop` min, no opera ese día.

OJO ZONA HORARIA: las velas de MT5 vienen en hora del SERVIDOR (UTC+3 en verano).
`or_open`/`session_close` se expresan en ESA hora (la de los datos). Verificado con
el histograma de volumen de US30 (pico 16-17h = apertura cash US). En invierno
(UTC+2) restar 1h al `or_open` (usar 15:30).

Ejecutar (MT5 abierto):
    python backtest/orb_backtester.py --symbol US30  --days 365
    python backtest/orb_backtester.py --symbol USTEC --days 365 --rr 2 --ormin 15
    python backtest/orb_backtester.py --symbol US500 --rr 0          (sin target, EOD)
    python backtest/orb_backtester.py --symbol EURUSD --oropen 10:00 (London open ~7 UTC)
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
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

logger = logging.getLogger(__name__)


def _risk_acc_per_lot(symbol: str, risk_price: float, config: dict) -> float:
    """Moneda de cuenta que arriesga 1 lote (gold/JPY/forex/índices)."""
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


def _hm_to_min(hm: str) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def run_orb_backtest(mt5_connector, symbol, date_from, date_to, config,
                     or_open="16:30", or_min=15, rr=2.0,
                     timestop=90, session_close="22:00",
                     spread_price=None, comm_lot=None,
                     min_range_atr=0.25, max_range_atr=3.0,
                     mode="breakout", trail_atr=0.0, trend_ema=0, half=None,
                     eurusd=1.08, p_balance=200.0, risk_pct=0.01) -> dict:
    # mode:
    #   "breakout" = entrar al primer cierre fuera del rango (espera confirmación)
    #   "candle"   = estilo paper Zarattini: dirección = signo de la vela de apertura
    #                (close vs open del rango), entrar YA en la vela siguiente. Captura
    #                más del movimiento del día (entrada inmediata, no tardía).
    or_open_m  = _hm_to_min(or_open)
    or_end_m   = or_open_m + int(or_min)
    sess_cl_m  = _hm_to_min(session_close)
    timestop_m = or_end_m + int(timestop)
    if comm_lot is None:
        comm_lot = float(config.get("backtest", {}).get("commission_per_lot", 7.0))

    print("=" * 60)
    print(f"  BACKTEST ORB — {symbol}  (Opening Range Breakout)")
    print(f"  {date_from:%Y-%m-%d} → {date_to:%Y-%m-%d}")
    print(f"  OR: {or_open}–{or_min}min (hora servidor) | TP {('EOD' if rr<=0 else str(rr)+'R')} | "
          f"stop=lado opuesto | timestop {timestop}min | cierre {session_close}")
    print("=" * 60)

    df = mt5_connector.get_rates_range(symbol, "M5", date_from, date_to)
    if df.empty:
        print("  ERROR: sin datos M5. ¿MT5 abierto y símbolo existe?")
        return {}

    df["dt"]  = pd.to_datetime(df["time"])
    df["min"] = df["dt"].dt.hour * 60 + df["dt"].dt.minute
    df["day"] = df["dt"].dt.date
    # Filtro de tendencia: EMA sobre M5 (solo usa pasado → sin look-ahead).
    # Solo se permiten rupturas a favor (BUY si entry>EMA, SELL si entry<EMA).
    if trend_ema and trend_ema > 0:
        df["ema"] = df["close"].ewm(span=int(trend_ema), adjust=False).mean()
    # ATR diario aproximado para filtrar rangos anómalos (mediana del rango OR)
    if spread_price is None:
        try:
            tick = mt5_connector.get_current_price(symbol)
            spread_price = float(tick.get("spread", 0) or 0) if tick else 0.0
        except Exception:
            spread_price = 0.0

    days = sorted(df["day"].unique())
    if half in ("first", "second"):
        mid = len(days) // 2
        days = days[:mid] if half == "first" else days[mid:]
        print(f"  [OOS split: mitad {half} → {len(days)} sesiones]")
    print(f"  Datos: {len(df):,} velas M5 | {len(days)} sesiones")

    trades   = []
    equity   = [p_balance]
    or_ranges = []
    discards = {"no_or": 0, "tight": 0, "wide": 0, "no_break": 0, "risk0": 0, "trend": 0}

    for day in days:
        d = df[df["day"] == day]
        or_bars = d[(d["min"] >= or_open_m) & (d["min"] < or_end_m)]
        if or_bars.empty:
            discards["no_or"] += 1
            continue
        or_high   = float(or_bars["high"].max())
        or_low    = float(or_bars["low"].min())
        or_open_px  = float(or_bars["open"].iloc[0])
        or_close_px = float(or_bars["close"].iloc[-1])
        or_rng  = or_high - or_low
        if or_rng <= 0:
            discards["no_or"] += 1
            continue

        # Filtro de rango: descartar días con rango de apertura anómalo
        med = float(np.median(or_ranges)) if len(or_ranges) >= 20 else None
        if med:
            if or_rng < med * min_range_atr:
                or_ranges.append(or_rng); discards["tight"] += 1; continue
            if or_rng > med * max_range_atr:
                or_ranges.append(or_rng); discards["wide"] += 1; continue
        or_ranges.append(or_rng)

        post = d[(d["min"] >= or_end_m) & (d["min"] < min(sess_cl_m, timestop_m))]
        entry = direction = entry_idx = None
        if mode == "candle":
            # Estilo paper: dirección = signo de la vela de apertura; entrar YA
            if post.empty or or_close_px == or_open_px:
                discards["no_break"] += 1
                continue
            direction = "BUY" if or_close_px > or_open_px else "SELL"
            entry     = float(post["open"].iloc[0])
            entry_idx = post.index[0]
        else:
            # Breakout: primer CIERRE fuera del rango, dentro del time-stop
            for idx, b in post.iterrows():
                c = float(b["close"])
                if c > or_high:
                    direction, entry, entry_idx = "BUY", c, idx; break
                if c < or_low:
                    direction, entry, entry_idx = "SELL", c, idx; break
            if entry is None:
                discards["no_break"] += 1
                continue

        # Filtro de tendencia: solo a favor de la EMA M5
        if trend_ema and trend_ema > 0:
            ema_val = float(df.loc[entry_idx, "ema"])
            if (direction == "BUY" and entry < ema_val) or \
               (direction == "SELL" and entry > ema_val):
                discards["trend"] += 1
                continue

        stop = or_low if direction == "BUY" else or_high
        risk = abs(entry - stop)
        if risk <= 0:
            discards["risk0"] += 1
            continue
        target = None
        if rr > 0:
            target = entry + rr * risk if direction == "BUY" else entry - rr * risk

        # Simular desde la vela siguiente al fill hasta el cierre de sesión
        rest = d[(d.index > entry_idx) & (d["min"] < sess_cl_m)]
        result, exit_px = "EOD", entry
        for _, b in rest.iterrows():
            hi, lo = float(b["high"]), float(b["low"])
            if direction == "BUY":
                if lo <= stop:               result, exit_px = "LOSS", stop; break
                if target and hi >= target:  result, exit_px = "WIN",  target; break
            else:
                if hi >= stop:               result, exit_px = "LOSS", stop; break
                if target and lo <= target:  result, exit_px = "WIN",  target; break
        if result == "EOD":
            exit_px = float(rest["close"].iloc[-1]) if not rest.empty else entry

        # PnL en R
        if result == "WIN":
            pnl_r = rr
        elif result == "LOSS":
            pnl_r = -1.0
        else:  # EOD: PnL realizado en múltiplos de R (la asimetría del paper)
            pnl_r = ((exit_px - entry) / risk) * (1 if direction == "BUY" else -1)

        # Coste en R: spread + comisión
        risk_acc_lot = _risk_acc_per_lot(symbol, risk, config)
        cost_r = (spread_price / risk if risk > 0 else 0.0) + \
                 (comm_lot / risk_acc_lot if risk_acc_lot > 0 else 0.0)
        pnl_r -= cost_r

        # Equity cuenta personal (EUR): 1% por trade, suelo lote 0.01
        eq = equity[-1]
        if risk_acc_lot > 0:
            lots = max(0.01, np.floor((eq * risk_pct) / risk_acc_lot / 0.01) * 0.01)
        else:
            lots = 0.01
        risk_eur = lots * risk_acc_lot
        pnl_eur  = pnl_r * risk_eur
        equity.append(eq + pnl_eur)

        trades.append({
            "day": str(day), "direction": direction,
            "entry": round(entry, 5), "stop": round(stop, 5),
            "or_range": round(or_rng, 5), "result": result,
            "pnl_r": round(pnl_r, 3), "lots": round(lots, 2),
            "pnl_eur": round(pnl_eur, 2),
        })

    return _report(trades, equity, discards, date_from, date_to,
                   p_balance, symbol, rr)


def _report(trades, equity, discards, date_from, date_to, balance0, symbol, rr):
    decided = [t for t in trades if t["result"] in ("WIN", "LOSS")]
    eod     = [t for t in trades if t["result"] == "EOD"]
    wins    = [t for t in trades if t["pnl_r"] > 0]
    wr_all  = len(wins) / len(trades) if trades else 0
    gp = sum(t["pnl_r"] for t in trades if t["pnl_r"] > 0)
    gl = abs(sum(t["pnl_r"] for t in trades if t["pnl_r"] < 0))
    pf = gp / gl if gl > 0 else float("inf")
    net = sum(t["pnl_r"] for t in trades)

    eq = np.array(equity)
    peak = np.maximum.accumulate(eq)
    dd = float(((peak - eq) / peak).max()) if len(eq) > 1 else 0.0
    days = max(1, (date_to - date_from).days)

    metrics = {
        "symbol": symbol, "model": "ORB", "rr_target": rr,
        "period": f"{date_from:%Y-%m-%d} → {date_to:%Y-%m-%d}",
        "trades": len(trades), "wins": len(wins),
        "win_rate": round(wr_all, 4),
        "profit_factor": round(pf, 2) if pf != float("inf") else 99.0,
        "net_r": round(net, 1), "max_drawdown": round(dd, 4),
        "trades_week": round(len(trades) / days * 7, 1),
        "eod_exits": len(eod), "decided_exits": len(decided),
        "equity_final": round(float(eq[-1]), 2),
        "return_pct": round((float(eq[-1]) / balance0 - 1) * 100, 1),
        "avg_win_r": round(np.mean([t["pnl_r"] for t in wins]), 2) if wins else 0,
        "avg_loss_r": round(np.mean([t["pnl_r"] for t in trades if t["pnl_r"] < 0]), 2)
                      if any(t["pnl_r"] < 0 for t in trades) else 0,
        "discards": discards,
    }

    print()
    print("=" * 60)
    print(f"  RESULTADOS ORB — {symbol}  (TP {('EOD' if rr<=0 else str(rr)+'R')})")
    print("=" * 60)
    print(f"  Operaciones:    {len(trades)}  (~{metrics['trades_week']}/semana)")
    print(f"  Win Rate:       {wr_all:.1%}  (rentable por asimetría, no por WR alto)")
    print(f"  Profit Factor:  {metrics['profit_factor']}")
    print(f"  Neto:           {net:+.1f} R   | avg win {metrics['avg_win_r']}R / avg loss {metrics['avg_loss_r']}R")
    print(f"  Max Drawdown:   {dd:.1%}  (cuenta {balance0:.0f} EUR @1%)")
    print(f"  Equity final:   {metrics['equity_final']:.2f} EUR ({metrics['return_pct']:+.1f}%)")
    print(f"  Salidas EOD:    {len(eod)} | por SL/TP: {len(decided)}")
    print(f"  Descartes:      {discards}")
    print("=" * 60)
    ok = (len(trades) >= 40 and pf > 1.0 and net > 0)
    if ok:
        print(f"  ✅ PASA el filtro base (≥40 ops, PF>1, neto+). Revalidar OOS.")
    else:
        print(f"  ❌ NO pasa el filtro base.")
    print("=" * 60)

    os.makedirs(LOGS_DIR, exist_ok=True)
    safe = symbol.replace("/", "").replace(" ", "")
    out = os.path.join(LOGS_DIR, f"backtest_orb_{safe}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"metrics": metrics, "trades": trades[-300:]}, f, indent=2)
    print(f"  Guardado: {out}")
    return {"metrics": metrics, "trades": trades, "equity_curve": equity}


if __name__ == "__main__":
    import yaml
    from data.mt5_connector import MT5Connector

    logging.basicConfig(level=logging.WARNING)
    cfg = yaml.safe_load(open(os.path.join(_PROJECT_ROOT, "config.yaml"),
                              encoding="utf-8"))

    def arg(flag, default=None, cast=str):
        return cast(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default

    symbol  = arg("--symbol", cfg["symbols"]["primary"])
    days    = arg("--days", 365, int)
    or_open = arg("--oropen", "16:30")
    or_min  = arg("--ormin", 15, int)
    rr      = arg("--rr", 2.0, float)
    timestop = arg("--timestop", 90, int)
    sclose  = arg("--close", "22:00")
    spread  = arg("--spread", None, float)
    comm    = arg("--comm", None, float)
    mode    = arg("--mode", "breakout")
    trend   = arg("--trend", 0, int)
    half    = arg("--half", None)

    conn = MT5Connector(cfg)
    if not conn.connect():
        print("ERROR: MT5 no disponible. ¿Está abierto?")
        sys.exit(1)
    eurusd = 1.08
    try:
        et = conn.get_current_price("EURUSD")
        if et: eurusd = float(et["bid"]) or 1.08
    except Exception:
        pass

    to = datetime.now(timezone.utc); fr = to - timedelta(days=days)
    run_orb_backtest(conn, symbol, fr, to, cfg, or_open=or_open, or_min=or_min,
                     rr=rr, timestop=timestop, session_close=sclose,
                     spread_price=spread, comm_lot=comm, mode=mode,
                     trend_ema=trend, half=half, eurusd=eurusd)
    conn.disconnect()

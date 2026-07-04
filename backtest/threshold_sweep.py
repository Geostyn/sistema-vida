"""
threshold_sweep — B7 del PLAN-FABLE: barrido post-hoc de `min_confluences`.

Toma el `signals_log` de un run de candidatos del unified_backtester
(`--min-confluences 0 --candidates`) y re-simula la EJECUCIÓN para cada
umbral t, con las mismas puertas que `run_unified` (sesión, max_simultaneous,
cierre H1 exacto, la LIMIT ocupa slot hasta expirar) y el mismo modelo de
costes (comisión por lote estimado).

⚠️ ESCALA OFFLINE: umbral_vivo ≈ umbral_offline + 0.7 (replay_validate.py).
   NUNCA copiar un umbral de aquí a config.yaml sin esa traducción.
⚠️ Caveat dedup: el signals_log viene de un run a min_conf=0, así que la
   cadena `_is_duplicate` del engine se calculó con TODAS las señales; un run
   nativo a umbral t puede emitir alguna señal distinta. Verificar con
   --native-* contra un run nativo (baseline B8) — tolerancia ±1-2 trades.

Uso (sin MT5, con el bot vivo):
    python backtest/threshold_sweep.py \
        --cand-is  backtest/results/unified_cand_is_*.json \
        --cand-oos backtest/results/unified_cand_oos_*.json \
        [--native-is ...baseline_is....json] [--native-oos ...] \
        [--tmin 4.0 --tmax 8.0 --tstep 0.5 --t-base 6.0]
"""

import os
import sys
import json
import glob
import argparse
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.historical_connector import HistoricalConnector
from backtest.backtester import _simulate_trade_managed, _calculate_metrics
from backtest.unified_backtester import load_config, RESULTS_DIR


def resim(signals_log: list, h1_full: pd.DataFrame, h1_opens: np.ndarray,
          config: dict, threshold: float) -> dict:
    """Re-simula la ejecución del run de candidatos filtrando confluences >= t.
    Réplica exacta del bloque de ejecución de run_unified()."""
    risk_cfg    = config.get("risk", {})
    sess        = config.get("sessions", {})
    max_sim     = int(risk_cfg.get("max_simultaneous", 2))
    risk_pct    = float(risk_cfg.get("risk_per_trade", 0.01))
    capital0    = float(risk_cfg.get("capital", 10000))
    mgmt        = config.get("trading", {}).get("management", {}) or {}
    managed     = bool(mgmt.get("enabled", True))
    partial_pct = float(mgmt.get("partial_close_pct", 0.5))
    trail_mult  = float(mgmt.get("trail_atr_mult", 2.0))
    fill_window = int(config.get("trading", {}).get("order_expiry_hours", 8))
    comm_lot    = float(config.get("backtest", {}).get("commission_per_lot", 7.0))

    trades, equity, active = [], [capital0], []
    n_sig = n_sess = 0

    for rec in signals_log:
        if float(rec["confluences"]) < threshold:
            continue
        n_sig += 1
        t_close = pd.Timestamp(rec["bar_time_server"]) + pd.Timedelta(hours=1)
        on_h1_close = (t_close.minute == 0)

        bar_idx = int(np.searchsorted(h1_opens, np.datetime64(t_close),
                                      side="right")) - 1
        while bar_idx >= 0 and pd.Timestamp(h1_full["time"].iloc[bar_idx]) \
                + pd.Timedelta(hours=1) > t_close:
            bar_idx -= 1

        active = [e for e in active if e > bar_idx]
        if not (rec["in_session"] and on_h1_close and bar_idx >= 0
                and len(active) < max_sim):
            continue
        n_sess += 1
        outcome = _simulate_trade_managed(
            h1_full, bar_idx, rec["direction"], float(rec["entry"]),
            float(rec["sl"]), float(rec["tp1"]),
            float(rec["tp2"] or rec["tp1"]), float(rec["atr"]),
            managed=managed, partial_pct=partial_pct,
            trail_mult=trail_mult, fill_window=fill_window, max_bars=80,
        )
        if outcome["result"] == "NO_FILL":
            active.append(bar_idx + fill_window)
        elif outcome["result"] != "OPEN":
            active.append(bar_idx + max(1, outcome["bars"]))
            sl_dist      = abs(float(rec["entry"]) - float(rec["sl"]))
            value_per_pt = 100.0
            lot_est      = (equity[-1] * risk_pct) / (sl_dist * value_per_pt) \
                if sl_dist > 0 else 0.01
            comm_pct     = (lot_est * comm_lot) / equity[-1]
            pnl_pct      = outcome["pnl_r"] * risk_pct - comm_pct
            equity.append(equity[-1] * (1 + pnl_pct))
            trades.append({
                "entry_time":    rec["bar_time_server"],
                "exit_time":     outcome["exit_time"],
                "direction":     rec["direction"],
                "hour_utc":      rec["utc_hour"],
                "result":        outcome["result"],
                "pnl_rr":        round(outcome["pnl_r"], 2),
                "pnl_pct":       round(pnl_pct * 100, 3),
                "bars_to_close": outcome["bars"],
                "confluences":   rec["confluences"],
                "rr":            rec["rr"],
                "bias":          rec["bias_h4"],
            })

    m = _calculate_metrics(trades, equity) if trades else {"error": "sin trades"}
    return {"threshold": threshold, "signals": n_sig, "attempted": n_sess,
            "metrics": m}


def better(m: dict, base: dict) -> bool:
    """Guardarraíles de optimize.py (anti-overfit)."""
    return (m.get("profit_factor", 0) >= base.get("profit_factor", 0) + 0.05 and
            m.get("total_return_pct", 0) >= base.get("total_return_pct", 0) + 8 and
            m.get("max_drawdown", 1) <= base.get("max_drawdown", 1) + 0.02 and
            m.get("total_trades", 0) >= base.get("total_trades", 0) * 0.7)


def _load_run(path_glob: str) -> dict:
    paths = sorted(glob.glob(path_glob))
    if not paths:
        raise FileNotFoundError(path_glob)
    with open(paths[-1], "r", encoding="utf-8") as f:
        return json.load(f)


def _row(r: dict) -> str:
    m = r["metrics"]
    if "error" in m:
        return (f"  {r['threshold']:>4.1f} {r['signals']:>6} {r['attempted']:>5} "
                f"{'—':>5} (sin trades)")
    return (f"  {r['threshold']:>4.1f} {r['signals']:>6} {r['attempted']:>5} "
            f"{m['total_trades']:>5} {m['win_rate']:>6.1%} "
            f"{m['profit_factor']:>6.2f} {m['total_return_pct']:>8.1f}% "
            f"{m['max_drawdown']:>6.1%} {m['avg_r_per_trade']:>6.2f} "
            f"{m['sharpe_ratio']:>6.2f}")


HDR = (f"  {'t':>4} {'señal':>6} {'ejec':>5} {'Trd':>5} {'WR':>6} "
       f"{'PF':>6} {'Ret':>9} {'DD':>6} {'avgR':>6} {'Sharpe':>6}")


def sweep_window(name: str, cand: dict, thresholds: list, config: dict,
                 h1_full, h1_opens, t_base: float, native: dict | None) -> dict:
    print(f"\n═══ {name}: {cand['from']} → {cand['to']} "
          f"({cand['signals']} señales candidatas) ═══")
    print(HDR)
    results = []
    for t in thresholds:
        r = resim(cand["signals_log"], h1_full, h1_opens, config, t)
        results.append(r)
        print(_row(r))

    base = next((r for r in results if abs(r["threshold"] - t_base) < 1e-9), None)
    out = {"window": name, "from": cand["from"], "to": cand["to"],
           "t_base": t_base, "results": results}

    if native is not None:
        nm = native["metrics"]
        ph = base["metrics"] if base else {}
        print(f"\n  Verificación vs run NATIVO t={native.get('min_confluences')}: "
              f"nativo {nm.get('total_trades', '—')} trades / post-hoc "
              f"{ph.get('total_trades', '—')} trades "
              f"(PF {nm.get('profit_factor', '—')} vs {ph.get('profit_factor', '—')})")
        out["native_check"] = {
            "native_trades": nm.get("total_trades"),
            "posthoc_trades": ph.get("total_trades"),
            "native_pf": nm.get("profit_factor"),
            "posthoc_pf": ph.get("profit_factor"),
        }

    if base and "error" not in base["metrics"]:
        winners = [r["threshold"] for r in results
                   if r["threshold"] != t_base and "error" not in r["metrics"]
                   and better(r["metrics"], base["metrics"])]
        print(f"  Umbrales que superan los guardarraíles vs t={t_base}: "
              f"{winners if winners else 'NINGUNO'}")
        out["winners"] = winners
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cand-is", required=True)
    ap.add_argument("--cand-oos", default=None)
    ap.add_argument("--native-is", default=None)
    ap.add_argument("--native-oos", default=None)
    ap.add_argument("--tmin", type=float, default=4.0)
    ap.add_argument("--tmax", type=float, default=8.0)
    ap.add_argument("--tstep", type=float, default=0.5)
    ap.add_argument("--t-base", type=float, default=None,
                    help="umbral baseline (default: risk.min_confluences)")
    args = ap.parse_args()

    config = load_config()
    t_base = args.t_base if args.t_base is not None \
        else float(config["risk"].get("min_confluences", 6.0))
    thresholds = [round(t, 2) for t in
                  np.arange(args.tmin, args.tmax + args.tstep / 2, args.tstep)]
    if t_base not in thresholds:
        thresholds = sorted(thresholds + [t_base])

    hc = HistoricalConnector()
    h1_entry = hc._load("XAUUSD", "H1")
    if h1_entry is None:
        raise RuntimeError("Falta caché XAUUSD_H1 — corre preload_history.py")
    h1_full, h1_opens = h1_entry["df"], h1_entry["open_ns"]

    report = {"tool": "threshold_sweep", "t_base": t_base,
              "thresholds": thresholds, "windows": [],
              "nota": "escala OFFLINE — vivo ≈ offline + 0.7 (replay_validate)"}

    cand_is = _load_run(args.cand_is)
    native_is = _load_run(args.native_is) if args.native_is else None
    report["windows"].append(sweep_window(
        "IS", cand_is, thresholds, config, h1_full, h1_opens, t_base, native_is))

    if args.cand_oos:
        cand_oos = _load_run(args.cand_oos)
        native_oos = _load_run(args.native_oos) if args.native_oos else None
        report["windows"].append(sweep_window(
            "OOS", cand_oos, thresholds, config, h1_full, h1_opens, t_base,
            native_oos))

        # Veredicto conjunto: guardarraíles en AMBAS ventanas
        sets = [set(w.get("winners", [])) for w in report["windows"]]
        both = sorted(sets[0] & sets[1]) if len(sets) == 2 else []
        report["winners_both_windows"] = both
        print(f"\n{'═' * 70}\n  VEREDICTO: umbrales que baten t={t_base} en "
              f"AMBAS ventanas: {both if both else 'NINGUNO'}")
        if not both:
            print("  → el umbral actual se queda como está (anti-overfit).")
        else:
            print("  ⚠️ escala OFFLINE: en vivo equivale a t+0.7 — validar antes "
                  "de tocar config.yaml.")
        print("═" * 70)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(RESULTS_DIR, f"threshold_sweep_{stamp}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False, default=str)
    print(f"\nGuardado: {out_path}")


if __name__ == "__main__":
    main()

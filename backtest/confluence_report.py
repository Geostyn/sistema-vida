"""
confluence_report — ¿Qué confluencia aporta y cuál mete ruido? (promesa §6.1)

Cruza el `signals_log` (componentes por señal) con `trades` (outcome) de runs
del unified_backtester y compara WR / avgR de los trades CON vs SIN cada
componente, en las dos ventanas (IS + OOS). Un componente solo es señal real
si ayuda (o estorba) en AMBAS ventanas — lo mixto es ruido/muestra corta.

READ-ONLY: no propone cambios de pesos; cuantifica. Cualquier cambio de
scoring exige después validación 2 ventanas con guardarraíles (PLAN-FABLE §8).

Uso:
    python backtest/confluence_report.py \
        --run backtest/results/unified_cand_is_*.json \
        --run backtest/results/unified_cand_oos_*.json
"""

import os
import sys
import json
import glob
import argparse
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest.unified_backtester import RESULTS_DIR

# (nombre, funcion sobre el rec de signals_log) — True = componente presente
FEATURES = [
    ("dxy_aligned",   lambda r: bool(r.get("dxy_aligned"))),
    ("mtf_aligned",   lambda r: bool(r.get("mtf_aligned"))),
    ("m15_aligned",   lambda r: bool(r.get("m15_aligned"))),
    ("sweep>0",       lambda r: (r.get("sweep_score") or 0) > 0),
    ("fvg>0",         lambda r: (r.get("fvg_score") or 0) > 0),
    ("pairs>0",       lambda r: (r.get("pairs_score") or 0) > 0),
    ("rsi_extremo",   lambda r: r.get("rsi_state") in ("OVERSOLD", "OVERBOUGHT")),
    ("regime_TREND",  lambda r: str(r.get("regime", "")).startswith("TRENDING")),
    ("adx>=25",       lambda r: (r.get("regime_adx") or 0) >= 25),
    ("entry_retroceso", lambda r: r.get("entry_type") == "retroceso"),
]


def _load(path_glob: str) -> dict:
    paths = sorted(glob.glob(path_glob))
    if not paths:
        raise FileNotFoundError(path_glob)
    with open(paths[-1], "r", encoding="utf-8") as f:
        return json.load(f)


def executed_with_features(run: dict) -> list[dict]:
    """Join trades ← signals_log por (entry_time, direction)."""
    sigs = {(s["bar_time_server"], s["direction"]): s
            for s in run["signals_log"] if s.get("executed")}
    out = []
    for t in run["trades"]:
        if t["result"] not in ("WIN", "LOSS"):
            continue
        s = sigs.get((t["entry_time"], t["direction"]))
        if s:
            out.append({**s, "result": t["result"], "pnl_rr": t["pnl_rr"]})
    return out


def stats(rows: list[dict]) -> tuple[int, float, float]:
    n = len(rows)
    if n == 0:
        return 0, float("nan"), float("nan")
    wr = sum(1 for r in rows if r["result"] == "WIN") / n
    avg = float(np.mean([r["pnl_rr"] for r in rows]))
    return n, wr, avg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True,
                    help="JSON del unified (repetir para IS y OOS)")
    args = ap.parse_args()

    windows = []
    for g in args.run:
        run = _load(g)
        rows = executed_with_features(run)
        label = f"{run['from'][:10]}→{run['to'][:10]}"
        windows.append((label, rows))
        n, wr, avg = stats(rows)
        # Correlación score total ↔ resultado (¿el score predice?)
        conf = np.array([r["confluences"] for r in rows], float)
        pnl = np.array([r["pnl_rr"] for r in rows], float)
        rho = float(np.corrcoef(conf, pnl)[0, 1]) if len(rows) > 2 else float("nan")
        print(f"\n═══ {label}: {n} trades WIN/LOSS | WR {wr:.1%} | "
              f"avgR {avg:+.2f} | corr(confluences, pnl_r) = {rho:+.3f} ═══")
        print(f"  {'componente':<16} {'n✓':>4} {'WR✓':>7} {'avgR✓':>7} "
              f"{'n✗':>4} {'WR✗':>7} {'avgR✗':>7}  Δ'WR'")
        for name, fn in FEATURES:
            con = [r for r in rows if fn(r)]
            sin = [r for r in rows if not fn(r)]
            nc, wc, ac = stats(con)
            ns, ws, as_ = stats(sin)
            d = (wc - ws) if nc and ns else float("nan")
            print(f"  {name:<16} {nc:>4} {wc:>7.1%} {ac:>+7.2f} "
                  f"{ns:>4} {ws:>7.1%} {as_:>+7.2f}  {d:+.0%}" if nc and ns else
                  f"  {name:<16} {nc:>4} {'—':>7} {'—':>7} {ns:>4}")

    # Consistencia entre ventanas
    if len(windows) >= 2:
        print(f"\n═══ CONSISTENCIA entre ventanas (ΔWR con-vs-sin, misma dirección) ═══")
        for name, fn in FEATURES:
            ds = []
            for _, rows in windows[:2]:
                con = [r for r in rows if fn(r)]
                sin = [r for r in rows if not fn(r)]
                nc, wc, _ = stats(con)
                ns, ws, _ = stats(sin)
                ds.append(wc - ws if (nc >= 8 and ns >= 8) else None)
            if any(d is None for d in ds):
                verdict = "muestra insuficiente (n<8 en un lado)"
            elif ds[0] > 0.03 and ds[1] > 0.03:
                verdict = f"AYUDA en ambas ({ds[0]:+.0%} / {ds[1]:+.0%})"
            elif ds[0] < -0.03 and ds[1] < -0.03:
                verdict = f"ESTORBA en ambas ({ds[0]:+.0%} / {ds[1]:+.0%})"
            else:
                verdict = f"mixto/neutro ({ds[0]:+.0%} / {ds[1]:+.0%})"
            print(f"  {name:<16} {verdict}")

    out = os.path.join(RESULTS_DIR,
                       f"confluence_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    print(f"\n(volcado no persistido — el informe vive en la salida y el handoff)")


if __name__ == "__main__":
    main()

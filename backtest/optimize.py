"""
Optimizador de gestión — busca, experimenta y mide mejoras en los parámetros
que el backtester SÍ honra (SL, R:R, parcial, trailing del runner). One-factor-
at-a-time alrededor del baseline, sobre 1 año XAUUSD H1, con comisiones.

Disciplina (protege el sistema validado WR 42.1% / PF 1.61):
  - Compara cada variante contra el BASELINE (config actual).
  - Recomienda un cambio SOLO si mejora de forma clara (PF y retorno arriba,
    DD no peor de +2pp, nº de trades suficiente) — evita curve-fitting marginal.
  - El ganador se valida luego out-of-sample antes de aplicarlo.

Uso:
    PY=/c/Users/geost/AppData/Local/Python/pythoncore-3.14-64/python.exe
    $PY backtest/optimize.py
"""

import os
import sys
import copy
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import logging
logging.disable(logging.CRITICAL)

import yaml
from data.mt5_connector import MT5Connector
from backtest.backtester import run_backtest

# Knobs a barrer (los que el backtester honra). Baseline marcado aparte.
SWEEP = {
    ("risk", "atr_sl_multiplier"):                 [1.3, 1.5, 1.7],
    ("risk", "min_rr"):                            [1.8, 2.0, 2.2, 2.5],
    ("trading", "management", "partial_close_pct"):[0.3, 0.4, 0.5, 0.6, 0.7],
    ("trading", "management", "trail_atr_mult"):   [1.5, 2.0, 2.5, 3.0],
}


def _set(cfg, path, value):
    d = cfg
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value


def _get(cfg, path, default=None):
    d = cfg
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def _run(conn, cfg, dt_from, dt_to):
    r = run_backtest(conn, "XAUUSD", "H1", dt_from, dt_to, copy.deepcopy(cfg),
                     use_filters=True, use_reversal=False)
    return r.get("metrics", {}) or {}


def _row(name, m):
    return (f"  {name:<34} {m.get('total_trades',0):>5} "
            f"{m.get('win_rate',0):>6.1%} {m.get('profit_factor',0):>6.2f} "
            f"{m.get('max_drawdown',0):>6.1%} {m.get('sharpe_ratio',0):>6.2f} "
            f"{m.get('total_return_pct',0):>+8.1f}%")


def main():
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    conn = MT5Connector(cfg)
    if not conn.connect():
        print("❌ MT5 no disponible"); return

    dt_to   = datetime.now(timezone.utc)
    dt_from = dt_to - timedelta(days=365)

    hdr = (f"  {'Variante':<34} {'Trd':>5} {'WR':>6} {'PF':>6} "
           f"{'MaxDD':>6} {'Shrp':>6} {'Retorno':>9}")

    print("═" * 78)
    print("  OPTIMIZADOR DE GESTIÓN — 1 año XAUUSD H1 (filtros ON, reversal OFF)")
    print("═" * 78)

    # ── Baseline ──────────────────────────────────────────────────
    t0 = time.time()
    base = _run(conn, cfg, dt_from, dt_to)
    dt1 = time.time() - t0
    print(f"\n(1 backtest ≈ {dt1:.0f}s)\n")
    print(hdr); print("  " + "-" * 74)
    print(_row("BASELINE (config actual)", base))

    base_pf  = base.get("profit_factor", 0)
    base_ret = base.get("total_return_pct", 0)
    base_dd  = base.get("max_drawdown", 1)
    base_trd = base.get("total_trades", 0)

    # ── Sweeps one-factor-at-a-time ───────────────────────────────
    candidates = []  # (path, value, metrics)
    for path, values in SWEEP.items():
        baseval = _get(cfg, path)
        print(f"\n── Barrido {'.'.join(path)} (baseline={baseval}) " + "─" * 20)
        print(hdr); print("  " + "-" * 74)
        for v in values:
            if v == baseval:
                print(_row(f"{'.'.join(path[-2:])}={v}  (=baseline)", base))
                continue
            cfgv = copy.deepcopy(cfg)
            _set(cfgv, path, v)
            m = _run(conn, cfgv, dt_from, dt_to)
            print(_row(f"{'.'.join(path[-2:])}={v}", m))
            candidates.append((path, v, m))

    # ── Selección con guardarraíles anti-overfit ──────────────────
    def better(m):
        return (m.get("profit_factor", 0) >= base_pf + 0.05 and
                m.get("total_return_pct", 0) >= base_ret + 8 and
                m.get("max_drawdown", 1) <= base_dd + 0.02 and
                m.get("total_trades", 0) >= base_trd * 0.7)

    winners = [(p, v, m) for (p, v, m) in candidates if better(m)]
    winners.sort(key=lambda x: (x[2].get("profit_factor", 0),
                                x[2].get("total_return_pct", 0)), reverse=True)

    print("\n" + "═" * 78)
    if winners:
        print("  CAMBIOS QUE MEJORAN DE FORMA CLARA (ordenados por PF):")
        print("  " + "-" * 74)
        for p, v, m in winners:
            print(_row(f"{'.'.join(p[-2:])}={v}", m))
        bp, bv, bm = winners[0]
        print(f"\n  🏆 MEJOR CAMBIO INDIVIDUAL: {'.'.join(bp)} = {bv}")
        print(f"     PF {base_pf:.2f}→{bm['profit_factor']:.2f}  "
              f"Retorno {base_ret:+.1f}%→{bm['total_return_pct']:+.1f}%  "
              f"DD {base_dd:.1%}→{bm['max_drawdown']:.1%}")
        print(f"\n  APLICAR: poner {'.'.join(bp)} = {bv} en config.yaml y validar OOS.")
    else:
        print("  Ningún cambio individual supera el baseline con margen claro.")
        print("  → El sistema validado ya está bien ajustado en estos knobs.")
    print("═" * 78)

    conn.disconnect()


if __name__ == "__main__":
    main()

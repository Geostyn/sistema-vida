"""
Validación OUT-OF-SAMPLE del cambio ganador del optimizador.

El sweep eligió sobre el último año. Aquí se prueba el candidato sobre el AÑO
ANTERIOR (datos que no se usaron para elegir) → si también gana o empata,
no es curve-fitting y se puede aplicar.

Candidatos: baseline vs atr_sl=1.7 vs atr_sl=1.7+partial=0.3 vs partial=0.3.
Uso:  PY backtest/validate_change.py
"""

import os
import sys
import copy
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


def _set(cfg, path, value):
    d = cfg
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value


def _run(conn, cfg, a, b):
    r = run_backtest(conn, "XAUUSD", "H1", a, b, copy.deepcopy(cfg),
                     use_filters=True, use_reversal=False)
    return r.get("metrics", {}) or {}


def _row(name, m):
    return (f"  {name:<26} {m.get('total_trades',0):>5} "
            f"{m.get('win_rate',0):>6.1%} {m.get('profit_factor',0):>6.2f} "
            f"{m.get('max_drawdown',0):>6.1%} {m.get('sharpe_ratio',0):>6.2f} "
            f"{m.get('total_return_pct',0):>+8.1f}%")


def main():
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    conn = MT5Connector(cfg)
    if not conn.connect():
        print("❌ MT5 no disponible"); return

    now = datetime.now(timezone.utc)
    windows = {
        "AÑO ANTERIOR (OOS)": (now - timedelta(days=730), now - timedelta(days=365)),
        "ÚLTIMO AÑO (in-sample)": (now - timedelta(days=365), now),
    }

    variants = {
        "BASELINE":             [],
        "atr_sl=1.7":           [(("risk", "atr_sl_multiplier"), 1.7)],
        "atr_sl=1.7+partial0.3":[(("risk", "atr_sl_multiplier"), 1.7),
                                 (("trading", "management", "partial_close_pct"), 0.3)],
        "partial=0.3":          [(("trading", "management", "partial_close_pct"), 0.3)],
    }

    hdr = (f"  {'Variante':<26} {'Trd':>5} {'WR':>6} {'PF':>6} "
           f"{'MaxDD':>6} {'Shrp':>6} {'Retorno':>9}")

    for wname, (a, b) in windows.items():
        print("\n" + "═" * 72)
        print(f"  {wname}   ({a:%Y-%m-%d} → {b:%Y-%m-%d})")
        print("═" * 72)
        print(hdr); print("  " + "-" * 68)
        for vname, changes in variants.items():
            cfgv = copy.deepcopy(cfg)
            for path, val in changes:
                _set(cfgv, path, val)
            m = _run(conn, cfgv, a, b)
            print(_row(vname, m))

    conn.disconnect()
    print("\nRegla: aplicar el cambio solo si gana o empata en el AÑO ANTERIOR (OOS).")


if __name__ == "__main__":
    main()

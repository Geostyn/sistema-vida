"""
Experimento: filtro de volatilidad por percentil de ATR.

Research (mean-reversion falla en regime change a tendencia) + autopsia
(atr_pct r=-0.37). Compara baseline vs vol_filter con varios percentiles, en
último año Y año anterior (OOS). Activar solo si gana/empata en AMBOS.

Uso:  PY backtest/optimize_vol.py
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


def _run(conn, cfg, a, b):
    return run_backtest(conn, "XAUUSD", "H1", a, b, copy.deepcopy(cfg),
                        use_filters=True, use_reversal=False).get("metrics", {}) or {}


def _row(name, m):
    return (f"  {name:<22} {m.get('total_trades',0):>5} "
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
        "ULTIMO AÑO":   (now - timedelta(days=365), now),
        "AÑO ANTERIOR": (now - timedelta(days=730), now - timedelta(days=365)),
    }
    variants = {"baseline (sin filtro)": None, "vol P80": 0.80,
                "vol P85": 0.85, "vol P90": 0.90, "vol P95": 0.95}

    hdr = (f"  {'Variante':<22} {'Trd':>5} {'WR':>6} {'PF':>6} "
           f"{'MaxDD':>6} {'Shrp':>6} {'Retorno':>9}")

    for wname, (a, b) in windows.items():
        print("\n" + "═" * 74)
        print(f"  {wname}   ({a:%Y-%m-%d} → {b:%Y-%m-%d})")
        print("═" * 74)
        print(hdr); print("  " + "-" * 72)
        for vname, pct in variants.items():
            cfgv = copy.deepcopy(cfg)
            cfgv.setdefault("volatility", {})
            cfgv["volatility"]["vol_filter"] = pct is not None
            if pct is not None:
                cfgv["volatility"]["vol_pct_max"] = pct
            print(_row(vname, _run(conn, cfgv, a, b)))

    conn.disconnect()
    print("\nRegla: activar vol_filter solo si un percentil gana/empata en AMBOS años.")


if __name__ == "__main__":
    main()

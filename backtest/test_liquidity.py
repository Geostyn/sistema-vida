"""
Test del modelo "liquidez primero" (sweep de stops -> reversión).

Ya existe en el backtester como modelo reversal (_check_reversal_setup), hoy
DESACTIVADO (backtest 2026-06-12: PF 0.93, pierde). Aquí se re-mide con datos
frescos: baseline (solo OB) vs OB+reversal, en último año Y año anterior.
Si OB+reversal no mejora a baseline en AMBOS, la idea simple no vale (haría
falta refinarla: TP en el pool opuesto + filtros más duros).

Uso:  PY backtest/test_liquidity.py
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
        "ULTIMO AÑO":   (now - timedelta(days=365), now),
        "AÑO ANTERIOR": (now - timedelta(days=730), now - timedelta(days=365)),
    }
    hdr = (f"  {'Variante':<26} {'Trd':>5} {'WR':>6} {'PF':>6} "
           f"{'MaxDD':>6} {'Shrp':>6} {'Retorno':>9}")

    for wname, (a, b) in windows.items():
        print("\n" + "═" * 74)
        print(f"  {wname}   ({a:%Y-%m-%d} → {b:%Y-%m-%d})")
        print("═" * 74)
        print(hdr); print("  " + "-" * 72)
        base = run_backtest(conn, "XAUUSD", "H1", a, b, copy.deepcopy(cfg),
                            use_filters=True, use_reversal=False).get("metrics", {})
        rev  = run_backtest(conn, "XAUUSD", "H1", a, b, copy.deepcopy(cfg),
                            use_filters=True, use_reversal=True).get("metrics", {})
        print(_row("baseline (solo OB)", base))
        print(_row("OB + reversal (liquidez)", rev))

    conn.disconnect()
    print("\nVeredicto: si OB+reversal no bate a baseline en AMBOS años, la idea")
    print("simple de liquidez-reversión no vale tal cual; necesita refinamiento.")


if __name__ == "__main__":
    main()

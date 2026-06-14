"""
Comparación de variantes de filtros de liquidez (validación 2026-06-12).

  A. BASELINE   — sin filtros, sin reversal (referencia histórica PF 1.61)
  B. PRECISO    — displacement + veto HTF (sin sweep-contra masivo), sin reversal
  C. VETO DURO  — sweep-contra + displacement + HTF (elección usuario), sin reversal

Uso:  python backtest/compare_variants.py
"""

import sys
import os
import copy
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from data.mt5_connector import MT5Connector
from backtest.backtester import run_backtest


def main():
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    conn = MT5Connector(cfg)
    if not conn.connect():
        print("ERROR: MT5 no disponible")
        sys.exit(1)

    dt_to   = datetime.now(timezone.utc)
    dt_from = dt_to - timedelta(days=365)

    results = {}

    # A. Baseline puro
    print("\n" + "#" * 60 + "\n#  VARIANTE A — BASELINE (sin filtros)\n" + "#" * 60)
    r = run_backtest(conn, "XAUUSD", "H1", dt_from, dt_to, copy.deepcopy(cfg),
                     use_filters=False, use_reversal=False)
    results["A_baseline"] = r.get("metrics", {})

    # B. Preciso: displacement + HTF, sin sweep-contra
    print("\n" + "#" * 60 + "\n#  VARIANTE B — PRECISO (displacement + HTF)\n" + "#" * 60)
    cfg_b = copy.deepcopy(cfg)
    cfg_b["liquidity"]["sweep_against_veto"] = False
    r = run_backtest(conn, "XAUUSD", "H1", dt_from, dt_to, cfg_b,
                     use_filters=True, use_reversal=False)
    results["B_preciso"] = r.get("metrics", {})

    # C. Veto duro completo (config actual), sin reversal
    print("\n" + "#" * 60 + "\n#  VARIANTE C — VETO DURO (sweep + disp + HTF)\n" + "#" * 60)
    r = run_backtest(conn, "XAUUSD", "H1", dt_from, dt_to, copy.deepcopy(cfg),
                     use_filters=True, use_reversal=False)
    results["C_veto_duro"] = r.get("metrics", {})

    conn.disconnect()

    print("\n" + "=" * 72)
    print("  COMPARATIVA FINAL (1 año XAUUSD H1, sin modelo reversal)")
    print("=" * 72)
    print(f"  {'Variante':<14} {'Trades':>6} {'WR':>7} {'PF':>6} {'MaxDD':>7} "
          f"{'Sharpe':>7} {'Retorno':>9} {'VetosHTF':>9}")
    print("  " + "-" * 68)
    for name, m in results.items():
        if not m:
            continue
        print(f"  {name:<14} {m['total_trades']:>6} {m['win_rate']:>6.1%} "
              f"{m['profit_factor']:>6.2f} {m['max_drawdown']:>6.1%} "
              f"{m['sharpe_ratio']:>7.2f} {m['total_return_pct']:>+8.1f}% "
              f"{m.get('vetoed_by_htf', 0):>9}")
    print("=" * 72)


if __name__ == "__main__":
    main()

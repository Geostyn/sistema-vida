"""
Experimento: ¿ayuda el veto por ADX (no operar reversión en tendencia fuerte)?

Compara baseline (sin veto ADX) vs adx_veto ON con varios umbrales, sobre el
último año Y el año anterior (OOS). Aplica solo si gana o empata en AMBOS.
El backtester SÍ honra trend_filter (es de los pocos filtros compartidos con
el vivo), así que aquí la validación tiene valor real.

Uso:  PY backtest/optimize_adx.py
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
    r = run_backtest(conn, "XAUUSD", "H1", a, b, copy.deepcopy(cfg),
                     use_filters=True, use_reversal=False)
    return r.get("metrics", {}) or {}


def _row(name, m):
    return (f"  {name:<22} {m.get('total_trades',0):>5} "
            f"{m.get('win_rate',0):>6.1%} {m.get('profit_factor',0):>6.2f} "
            f"{m.get('max_drawdown',0):>6.1%} {m.get('sharpe_ratio',0):>6.2f} "
            f"{m.get('total_return_pct',0):>+8.1f}% "
            f"{m.get('vetoed_by_htf',0):>6}")


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
    variants = {"baseline (sin ADX)": None, "adx_max=25": 25.0,
                "adx_max=28": 28.0, "adx_max=30": 30.0, "adx_max=35": 35.0}

    hdr = (f"  {'Variante':<22} {'Trd':>5} {'WR':>6} {'PF':>6} "
           f"{'MaxDD':>6} {'Shrp':>6} {'Retorno':>9} {'Vetos':>6}")

    for wname, (a, b) in windows.items():
        print("\n" + "═" * 78)
        print(f"  {wname}   ({a:%Y-%m-%d} → {b:%Y-%m-%d})")
        print("═" * 78)
        print(hdr); print("  " + "-" * 76)
        for vname, thr in variants.items():
            cfgv = copy.deepcopy(cfg)
            cfgv.setdefault("trend_filter", {})
            cfgv["trend_filter"]["adx_veto"] = thr is not None
            if thr is not None:
                cfgv["trend_filter"]["adx_max"] = thr
            print(_row(vname, _run(conn, cfgv, a, b)))

    conn.disconnect()
    print("\nRegla: activar adx_veto solo si un umbral gana/empata en AMBAS ventanas.")


if __name__ == "__main__":
    main()

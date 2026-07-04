"""
Estudio de horas de sesión — ¿hay una ventana contigua mejor que 7-21 UTC,
robusta en el último año Y el año anterior (OOS)?

Replica el método del filtro de volatilidad: solo se recomienda un cambio si
gana/empata en AMBAS ventanas. El backtester filtra por rango contiguo
(sessions.allowed_hours_utc.start <= hora < end), así que probamos rangos.

Read-only sobre la estrategia: solo corre backtests y mide. NO cambia config.

Uso:  PY backtest/study_hours.py
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
import numpy as np
from data.mt5_connector import MT5Connector
from backtest.backtester import run_backtest


def _trades(conn, cfg, a, b):
    r = run_backtest(conn, "XAUUSD", "H1", a, b, copy.deepcopy(cfg),
                     use_filters=True, use_reversal=False)
    return r.get("trades", []) or [], r.get("metrics", {}) or {}


def _pnl(t):
    for k in ("pnl_rr", "pnl_r", "pnl_pct"):
        if k in t and t[k] is not None:
            return float(t[k])
    return 0.0


def _per_hour(trades):
    """{hora: (n, wins, sum_R)}"""
    out = {}
    for t in trades:
        h = int(t.get("hour_utc", -1))
        res = t.get("result", "")
        if res not in ("WIN", "LOSS"):
            continue
        n, w, s = out.get(h, (0, 0, 0.0))
        out[h] = (n + 1, w + (1 if res == "WIN" else 0), s + _pnl(t))
    return out


def main():
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    conn = MT5Connector(cfg)
    if not conn.connect():
        print("❌ MT5 no disponible"); return

    now = datetime.now(timezone.utc)
    win = {
        "Y1 (último año)": (now - timedelta(days=365), now),
        "Y2 (año anterior)": (now - timedelta(days=730), now - timedelta(days=365)),
    }

    # 1) Recoger trades de ambos años con la config actual (7-21)
    per = {}
    for name, (a, b) in win.items():
        tr, m = _trades(conn, cfg, a, b)
        per[name] = _per_hour(tr)

    # 2) Tabla por hora, lado a lado
    print("\n" + "═" * 64)
    print("  WR y R-neto por hora UTC (config actual 7-21)")
    print("═" * 64)
    print(f"  {'h':>3} | {'Y1 n':>5} {'Y1 WR':>6} {'Y1 R':>7} | "
          f"{'Y2 n':>5} {'Y2 WR':>6} {'Y2 R':>7} | robusto-malo")
    print("  " + "-" * 60)
    bad_both = []
    for h in range(7, 21):
        n1, w1, s1 = per["Y1 (último año)"].get(h, (0, 0, 0.0))
        n2, w2, s2 = per["Y2 (año anterior)"].get(h, (0, 0, 0.0))
        wr1 = f"{w1/n1:.0%}" if n1 else "  -"
        wr2 = f"{w2/n2:.0%}" if n2 else "  -"
        # malo robusto: R-neto < 0 en AMBOS años con muestra mínima
        flag = ""
        if n1 >= 3 and n2 >= 3 and s1 < 0 and s2 < 0:
            flag = "⚠️ MALO en ambos"
            bad_both.append(h)
        print(f"  {h:>3} | {n1:>5} {wr1:>6} {s1:>+7.1f} | "
              f"{n2:>5} {wr2:>6} {s2:>+7.1f} | {flag}")

    print(f"\n  Horas malas en AMBOS años: {bad_both or 'ninguna'}")

    # 3) Probar ventanas contiguas alternativas en AMBOS años
    print("\n" + "═" * 64)
    print("  Validación de ventanas contiguas (PF / Retorno / DD)")
    print("═" * 64)
    print(f"  {'Ventana':<12} {'Y1 PF':>6} {'Y1 Ret':>8} {'Y1 DD':>6} | "
          f"{'Y2 PF':>6} {'Y2 Ret':>8} {'Y2 DD':>6}")
    print("  " + "-" * 62)
    candidates = [(7, 21), (8, 21), (9, 21), (7, 20), (8, 20),
                  (7, 19), (8, 19), (7, 17), (8, 17), (13, 21)]
    base = None
    for (s, e) in candidates:
        cfgv = copy.deepcopy(cfg)
        cfgv.setdefault("sessions", {}).setdefault("allowed_hours_utc", {})
        cfgv["sessions"]["allowed_hours_utc"]["start"] = s
        cfgv["sessions"]["allowed_hours_utc"]["end"] = e
        _, m1 = _trades(conn, cfgv, *win["Y1 (último año)"])
        _, m2 = _trades(conn, cfgv, *win["Y2 (año anterior)"])
        tag = f"{s}-{e}"
        row = (f"  {tag:<12} {m1.get('profit_factor',0):>6.2f} "
               f"{m1.get('total_return_pct',0):>+7.1f}% {m1.get('max_drawdown',0):>5.1%} | "
               f"{m2.get('profit_factor',0):>6.2f} "
               f"{m2.get('total_return_pct',0):>+7.1f}% {m2.get('max_drawdown',0):>5.1%}")
        if (s, e) == (7, 21):
            base = (m1, m2)
            row += "  ← actual"
        print(row)

    conn.disconnect()
    print("\nRegla: cambiar la ventana SOLO si mejora PF/Retorno SIN subir DD en AMBOS años.")


if __name__ == "__main__":
    main()

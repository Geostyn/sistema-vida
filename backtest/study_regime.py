"""
Experimento: puerta de régimen — ¿ayuda saltar TODA señal cuando la tendencia es
FUERTE (ADX H4 alto), no solo las contra-tendencia?

Hipótesis (investigación web 2026-06-19 + autopsia): la reversión a Order Block es
mean-reversion; falla en tendencia fuerte EN CUALQUIER dirección (a favor también,
porque el precio no retrocede al OB, sigue). El `adx_veto` (solo contra-tendencia)
era redundante con counter_trend_veto; ESTO es distinto: bloquea ambas direcciones.

Compara baseline (sin puerta) vs `regime.block_strong_trend_adx` ∈ {28,32,36,40}
sobre el último año Y el anterior (OOS). Aplicar solo si gana/empata en AMBOS sin
subir el drawdown. El backtester honra esta puerta (config.regime.block_strong_trend_adx).

Uso:  PY backtest/study_regime.py   (requiere MT5 abierto y el bot vivo PARADO)
"""

import os
import sys
import copy
import io
import contextlib
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
    with contextlib.redirect_stdout(io.StringIO()):
        r = run_backtest(conn, "XAUUSD", "H1", a, b, copy.deepcopy(cfg),
                         use_filters=True, use_reversal=False)
    return r.get("metrics", {}) or {}


def main():
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    conn = MT5Connector(cfg)
    if not conn.connect():
        print("❌ MT5 no disponible"); return
    now = datetime.now(timezone.utc)
    W = {"Y1 (último año)": (now - timedelta(days=365), now),
         "Y2 (año anterior)": (now - timedelta(days=730), now - timedelta(days=365))}
    variants = {"baseline (sin puerta)": None, "ADX>=28": 28, "ADX>=32": 32,
                "ADX>=36": 36, "ADX>=40": 40}

    print(f"\n{'Variante':<22} | {'Y1 PF':>6} {'Y1 Ret':>8} {'Y1 DD':>6} {'Y1 n':>5} {'veto':>5} "
          f"| {'Y2 PF':>6} {'Y2 Ret':>8} {'Y2 DD':>6} {'Y2 n':>5} {'veto':>5}")
    print("-" * 104)
    rows = {}
    for name, thr in variants.items():
        cfgv = copy.deepcopy(cfg)
        cfgv.setdefault("regime", {})["block_strong_trend_adx"] = thr
        m1 = _run(conn, cfgv, *W["Y1 (último año)"])
        m2 = _run(conn, cfgv, *W["Y2 (año anterior)"])
        rows[name] = (m1, m2)
        print(f"{name:<22} | {m1.get('profit_factor',0):>6.2f} {m1.get('total_return_pct',0):>+7.1f}% "
              f"{m1.get('max_drawdown',0):>5.1%} {m1.get('total_trades',0):>5} {m1.get('vetoed_by_regime',0):>5} "
              f"| {m2.get('profit_factor',0):>6.2f} {m2.get('total_return_pct',0):>+7.1f}% "
              f"{m2.get('max_drawdown',0):>5.1%} {m2.get('total_trades',0):>5} {m2.get('vetoed_by_regime',0):>5}")

    conn.disconnect()

    # Veredicto: mejor variante que gana/empata PF y retorno en AMBOS años sin subir DD > +1pp
    b1, b2 = rows["baseline (sin puerta)"]
    best = None
    for name, (m1, m2) in rows.items():
        if name.startswith("baseline"):
            continue
        ok = (m1.get('profit_factor',0) >= b1.get('profit_factor',0) and
              m2.get('profit_factor',0) >= b2.get('profit_factor',0) and
              m1.get('total_return_pct',0) >= b1.get('total_return_pct',0) - 0.5 and
              m2.get('total_return_pct',0) >= b2.get('total_return_pct',0) - 0.5 and
              m1.get('max_drawdown',0) <= b1.get('max_drawdown',0) + 0.01 and
              m2.get('max_drawdown',0) <= b2.get('max_drawdown',0) + 0.01)
        if ok and (best is None or
                   (m1.get('profit_factor',0)+m2.get('profit_factor',0)) >
                   (rows[best][0].get('profit_factor',0)+rows[best][1].get('profit_factor',0))):
            best = name
    print("\n" + "=" * 60)
    if best:
        print(f"✅ GANADOR ROBUSTO: {best} → mejora PF en AMBOS años sin subir DD. "
              f"Aplicar `regime.block_strong_trend_adx`.")
    else:
        print("🔴 Ninguna variante gana/empata en AMBOS años sin coste. "
              "NO aplicar puerta de régimen (la base ya captura el edge).")


if __name__ == "__main__":
    main()

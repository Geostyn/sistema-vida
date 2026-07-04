"""
Experimento STANDALONE: ¿tienen edge de reversión las bandas de VWAP en XAUUSD H1?

Independiente de la lógica OB del bot (como test_liquidity.py): testea el PRINCIPIO
puro — fadear extremos de banda VWAP (±kσ) con objetivo en VWAP y SL por ATR — para
decidir si vale la pena integrarlo como confluencia/filtro en signal_engine.

Variantes:
  - k ∈ {1.5, 2.0, 2.5} desviaciones.
  - Filtro de régimen "VWAP plano" (|pendiente| < flat_atr × ATR): solo fadear en rango.
Validación OOS: último año Y año anterior. Solo vale si gana/empata en AMBOS.

Reglas de ejecución (fijas, conservadoras):
  - Sesión 7-21 UTC, salta hora 16 (consistente con el bot). Salta primeras 2 barras
    del día (std de VWAP inestable al abrir).
  - Entrada al CIERRE de la barra que toca la banda. 1 posición a la vez.
  - Objetivo = nivel VWAP al entrar. SL = entry ∓ sl_atr × ATR. Time stop = 24 barras.
  - R = (exit-entry)/riesgo (BUY) ó (entry-exit)/riesgo (SELL). Comisión ~0.04R.

Uso:  PY backtest/study_vwap.py   (MT5 abierto, bot vivo PARADO para evitar contención)
"""

import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import logging
logging.disable(logging.CRITICAL)

import numpy as np
import pandas as pd
import yaml
from data.mt5_connector import MT5Connector
from analysis.indicators import add_indicators
from analysis.vwap_engine import session_vwap_bands

SL_ATR     = 1.5
TIME_STOP  = 24
COMM_R     = 0.04   # coste aproximado en R por trade (spread+comisión)


def simulate(df, k, flat_atr=None):
    """Devuelve lista de pnl en R. flat_atr=None → sin filtro de régimen."""
    d = session_vwap_bands(df, k=k)
    d = d.reset_index(drop=True)
    hours = pd.to_datetime(d["time"]).dt.hour
    day = pd.to_datetime(d["time"]).dt.floor("D")
    bar_of_day = day.groupby(day).cumcount() if hasattr(day.groupby(day), "cumcount") else None
    # índice de barra dentro del día (para saltar las 2 primeras)
    bod = d.groupby(day).cumcount()

    trades = []
    i = 0
    n = len(d)
    while i < n - 1:
        row = d.iloc[i]
        h = int(hours.iloc[i])
        atr = float(row.get("atr", np.nan))
        if (not (7 <= h < 22)) or h == 16 or bod.iloc[i] < 2 or \
           np.isnan(atr) or atr <= 0 or np.isnan(row["vwap_std"]) or row["vwap_std"] <= 0:
            i += 1; continue

        # Filtro de régimen: VWAP plano (rango)
        if flat_atr is not None:
            slope = abs(float(row.get("vwap_slope", 0) or 0))
            if slope > flat_atr * atr:
                i += 1; continue

        close = float(row["close"])
        direction = None
        if close <= float(row["vwap_lower"]):
            direction = "BUY"
        elif close >= float(row["vwap_upper"]):
            direction = "SELL"
        if direction is None:
            i += 1; continue

        entry = close
        target = float(row["vwap"])
        if direction == "BUY":
            sl = entry - SL_ATR * atr
            risk = entry - sl
            if risk <= 0 or target <= entry:
                i += 1; continue
        else:
            sl = entry + SL_ATR * atr
            risk = sl - entry
            if risk <= 0 or target >= entry:
                i += 1; continue

        # Caminar adelante hasta target / sl / time stop
        outcome = None
        for j in range(i + 1, min(i + 1 + TIME_STOP, n)):
            hi = float(d.iloc[j]["high"]); lo = float(d.iloc[j]["low"])
            if direction == "BUY":
                if lo <= sl:
                    outcome = -1.0; break
                if hi >= target:
                    outcome = (target - entry) / risk; break
            else:
                if hi >= sl:
                    outcome = -1.0; break
                if lo <= target:
                    outcome = (entry - target) / risk; break
        if outcome is None:  # time stop → mark-to-market
            cexit = float(d.iloc[min(i + TIME_STOP, n - 1)]["close"])
            outcome = ((cexit - entry) if direction == "BUY" else (entry - cexit)) / risk
            j = min(i + TIME_STOP, n - 1)
        trades.append(outcome - COMM_R)
        i = j + 1  # no solapar posiciones
    return trades


def stats(trades):
    if not trades:
        return dict(n=0, wr=0, pf=0, exp=0)
    t = np.array(trades)
    wins = t[t > 0].sum()
    losses = -t[t < 0].sum()
    wr = float((t > 0).mean())
    pf = float(wins / losses) if losses > 0 else float("inf")
    return dict(n=len(t), wr=wr, pf=pf, exp=float(t.mean()))


def main():
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    conn = MT5Connector(cfg)
    if not conn.connect():
        print("❌ MT5 no disponible"); return
    now = datetime.now(timezone.utc)
    windows = {
        "Y1 (último año)":   (now - timedelta(days=365), now),
        "Y2 (año anterior)": (now - timedelta(days=730), now - timedelta(days=365)),
    }
    data = {}
    for name, (a, b) in windows.items():
        df = conn.get_rates_range("XAUUSD", "H1", a, b)
        if df is None or df.empty:
            print(f"sin datos {name}"); return
        data[name] = add_indicators(df)
    conn.disconnect()

    variants = [("k=1.5", 1.5, None), ("k=2.0", 2.0, None), ("k=2.5", 2.5, None),
                ("k=2.0 +flat", 2.0, 0.10), ("k=2.0 +flat0.05", 2.0, 0.05)]
    print(f"\n{'Variante':<18} | {'Y1 n':>5} {'Y1 WR':>6} {'Y1 PF':>6} {'Y1 exp':>7} "
          f"| {'Y2 n':>5} {'Y2 WR':>6} {'Y2 PF':>6} {'Y2 exp':>7}")
    print("-" * 92)
    res = {}
    for tag, k, flat in variants:
        s1 = stats(simulate(data["Y1 (último año)"], k, flat))
        s2 = stats(simulate(data["Y2 (año anterior)"], k, flat))
        res[tag] = (s1, s2)
        print(f"{tag:<18} | {s1['n']:>5} {s1['wr']:>6.1%} {s1['pf']:>6.2f} {s1['exp']:>+7.3f} "
              f"| {s2['n']:>5} {s2['wr']:>6.1%} {s2['pf']:>6.2f} {s2['exp']:>+7.3f}")

    print("\n" + "=" * 60)
    good = [t for t, (s1, s2) in res.items()
            if s1["n"] >= 20 and s2["n"] >= 20 and s1["pf"] >= 1.2 and s2["pf"] >= 1.2
            and s1["exp"] > 0 and s2["exp"] > 0]
    if good:
        print(f"✅ VWAP-reversión con edge OOS en: {good}. Vale integrarlo como "
              f"confluencia/filtro en signal_engine (validar combinado luego).")
    else:
        print("🔴 La reversión pura por bandas VWAP NO tiene edge robusto OOS en XAUUSD H1. "
              "No integrar como señal independiente (quizá solo como filtro de contexto).")


if __name__ == "__main__":
    main()

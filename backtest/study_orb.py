"""
Experimento STANDALONE: ¿tiene edge un stream de BREAKOUT (Initial Balance) en
XAUUSD H1? Idea de la investigación en comunidades (Forex Factory / Trade That
Swing): el bot es mean-reversion y PIERDE en tendencia; un breakout captura los
días de expansión/tendencia que el bot se pierde → complementario por régimen.

Initial Balance (IB) = rango de las 2 primeras barras H1 de la sesión (07:00-09:00
UTC). Durante 09:00-17:00 UTC, primer cierre fuera del IB → entrada en esa dirección.
SL = entry ∓ sl_frac×IB_range; TP = entry ± tp_mult×IB_range; time-stop al cierre de
sesión. 1 trade/día. R = (exit-entry)/riesgo. Coste ~0.04R.

Validación OOS: último año Y año anterior. Solo vale si gana/empata en AMBOS.
⚠️ Trampa del breakout: brilla en años de tendencia, sangra en chop → por eso 2 años.

Uso:  PY backtest/study_orb.py   (MT5 abierto, bot vivo PARADO para evitar contención)
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

SESSION_START = 7    # IB empieza
IB_BARS       = 2    # 07:00 + 08:00 → IB cubre 07-09
BREAK_FROM    = 9    # escaneo de ruptura desde esta hora
SESSION_END   = 17   # no entrar después; cerrar al final
COMM_R        = 0.04


def simulate(df, sl_frac, tp_mult, trend=False, lowvol=False):
    d = add_indicators(df).reset_index(drop=True)
    t = pd.to_datetime(d["time"])
    d["h"] = t.dt.hour
    d["day"] = t.dt.floor("D")
    trades = []
    ib_ranges = []  # para filtro lowvol (mediana móvil de rangos IB)
    for day, g in d.groupby("day"):
        g = g.sort_values("h")
        ib = g[(g["h"] >= SESSION_START) & (g["h"] < SESSION_START + IB_BARS)]
        if len(ib) < IB_BARS:
            continue
        ib_high = float(ib["high"].max()); ib_low = float(ib["low"].min())
        ib_range = ib_high - ib_low
        if ib_range <= 0:
            continue
        # filtro lowvol: IB de hoy < mediana de los últimos 20 IB
        lv_ok = True
        if lowvol:
            if len(ib_ranges) >= 20:
                lv_ok = ib_range < float(np.median(ib_ranges[-20:]))
            else:
                lv_ok = False
        ib_ranges.append(ib_range)
        if lowvol and not lv_ok:
            continue

        sess = g[(g["h"] >= BREAK_FROM) & (g["h"] <= SESSION_END)]
        if sess.empty:
            continue
        entry = sl = tp = None; direction = None; entry_idx = None
        rows = sess.reset_index(drop=True)
        for i in range(len(rows)):
            c = float(rows.loc[i, "close"])
            ema = float(rows.loc[i, "ema_200"]) if "ema_200" in rows and not pd.isna(rows.loc[i, "ema_200"]) else None
            if c > ib_high:
                if trend and ema is not None and c < ema:
                    continue
                direction = "BUY"; entry = c
                sl = entry - sl_frac * ib_range; tp = entry + tp_mult * ib_range
                entry_idx = i; break
            if c < ib_low:
                if trend and ema is not None and c > ema:
                    continue
                direction = "SELL"; entry = c
                sl = entry + sl_frac * ib_range; tp = entry - tp_mult * ib_range
                entry_idx = i; break
        if direction is None:
            continue
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        outcome = None
        for j in range(entry_idx + 1, len(rows)):
            hi = float(rows.loc[j, "high"]); lo = float(rows.loc[j, "low"])
            if direction == "BUY":
                if lo <= sl: outcome = -1.0; break
                if hi >= tp: outcome = (tp - entry) / risk; break
            else:
                if hi >= sl: outcome = -1.0; break
                if lo <= tp: outcome = (entry - tp) / risk; break
        if outcome is None:  # cierre de sesión
            cexit = float(rows.loc[len(rows) - 1, "close"])
            outcome = ((cexit - entry) if direction == "BUY" else (entry - cexit)) / risk
        trades.append(outcome - COMM_R)
    return trades


def stats(tr):
    if not tr:
        return dict(n=0, wr=0, pf=0, exp=0)
    a = np.array(tr)
    w = a[a > 0].sum(); l = -a[a < 0].sum()
    return dict(n=len(a), wr=float((a > 0).mean()),
                pf=float(w / l) if l > 0 else float("inf"), exp=float(a.mean()))


def main():
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    conn = MT5Connector(cfg)
    if not conn.connect():
        print("❌ MT5 no disponible"); return
    now = datetime.now(timezone.utc)
    W = {"Y1 (último año)": (now - timedelta(days=365), now),
         "Y2 (año anterior)": (now - timedelta(days=730), now - timedelta(days=365))}
    data = {}
    for name, (a, b) in W.items():
        df = conn.get_rates_range("XAUUSD", "H1", a, b)
        if df is None or df.empty:
            print("sin datos", name); return
        data[name] = df
    conn.disconnect()

    variants = [
        ("R:R1:2 base",        1.0, 2.0, False, False),
        ("R:R1:2 +trend",      1.0, 2.0, True,  False),
        ("R:R1:4 +trend",      0.5, 2.0, True,  False),
        ("R:R1:4 +trend+lowv", 0.5, 2.0, True,  True),
        ("R:R1:2 +trend+lowv", 1.0, 2.0, True,  True),
    ]
    print(f"\n{'Variante':<20} | {'Y1 n':>4} {'Y1 WR':>6} {'Y1 PF':>6} {'Y1 exp':>7} "
          f"| {'Y2 n':>4} {'Y2 WR':>6} {'Y2 PF':>6} {'Y2 exp':>7}")
    print("-" * 90)
    res = {}
    for tag, slf, tpm, tr, lv in variants:
        s1 = stats(simulate(data["Y1 (último año)"], slf, tpm, tr, lv))
        s2 = stats(simulate(data["Y2 (año anterior)"], slf, tpm, tr, lv))
        res[tag] = (s1, s2)
        print(f"{tag:<20} | {s1['n']:>4} {s1['wr']:>6.1%} {s1['pf']:>6.2f} {s1['exp']:>+7.3f} "
              f"| {s2['n']:>4} {s2['wr']:>6.1%} {s2['pf']:>6.2f} {s2['exp']:>+7.3f}")

    print("\n" + "=" * 60)
    good = [t for t, (s1, s2) in res.items()
            if s1["n"] >= 25 and s2["n"] >= 25 and s1["pf"] >= 1.2 and s2["pf"] >= 1.2
            and s1["exp"] > 0 and s2["exp"] > 0]
    if good:
        print(f"✅ Breakout IB con edge OOS en: {good}. Candidato a 3er stream "
              f"complementario (captura tendencia donde la mean-reversion pierde).")
    else:
        print("🔴 El breakout IB NO tiene edge robusto OOS en XAUUSD H1 (al menos en H1). "
              "Quizá necesite M15 o mejores filtros; no integrar tal cual.")


if __name__ == "__main__":
    main()

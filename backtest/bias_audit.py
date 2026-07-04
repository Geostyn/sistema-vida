"""
bias_audit — Fase C.1 del PLAN-FABLE: ¿el 0-BUY de junio es un bug o es el mercado?

Evalúa `compute_directional_bias(df_h4)` punto-en-tiempo (HistoricalConnector,
mismos 300 H4 + add_indicators que el vivo — el replay B6 confirmó bias offline
== vivo al 100%) en cada cierre H1 de la ventana, y lo contrasta con:
  - la distribución diaria BULLISH/BEARISH/NEUTRAL,
  - el cambio real del precio del oro ese día,
  - el retorno forward a 24 velas H1 (¿acierta el bias?).

Uso (sin MT5, con el bot vivo):
    python backtest/bias_audit.py --from 2026-06-01 --to 2026-07-03
"""

import os
import sys
import json
import argparse
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.historical_connector import HistoricalConnector
from backtest.unified_backtester import RESULTS_DIR
from analysis.indicators import add_indicators
from analysis.trend_filter import compute_directional_bias, detect_htf_trend


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="t_from", default="2026-06-01")
    ap.add_argument("--to", dest="t_to", default="2026-07-03")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--cadence", default="H1", choices=["H1", "H4"],
                    help="H4 para ventanas largas (6 evaluaciones/día)")
    args = ap.parse_args()

    hc = HistoricalConnector()
    offset = hc.utc_offset_hours()
    t_from = pd.Timestamp(args.t_from) + pd.Timedelta(hours=offset)
    t_to = pd.Timestamp(args.t_to) + pd.Timedelta(hours=offset) + pd.Timedelta(days=1)

    h1 = hc._load(args.symbol, "H1")["df"]

    rows = []
    closes = list(hc.iter_closes(args.symbol, args.cadence, t_from, t_to))
    print(f"Bias audit {args.symbol} | {args.t_from} → {args.t_to} "
          f"| {len(closes)} cierres {args.cadence}")
    for t_close in closes:
        hc.set_now(t_close)
        df_h4 = hc.get_rates(args.symbol, "H4", 300)
        if df_h4.empty:
            continue
        df_h4 = add_indicators(df_h4)
        bias, direction = compute_directional_bias(df_h4, lookback=5)
        htf = detect_htf_trend(df_h4)
        rows.append({
            "t_server": t_close,
            "date_utc": (t_close - pd.Timedelta(hours=offset)).date(),
            "bias": bias, "direction": direction, "htf_trend": htf,
        })

    df = pd.DataFrame(rows)

    # Retorno forward 24 H1 por barra (precio real, con futuro — solo evaluación)
    h1_t = h1.set_index("time")["close"]
    idx = h1_t.index.searchsorted(df["t_server"]) - 1
    idx = np.clip(idx, 0, len(h1_t) - 1)
    fwd_idx = np.clip(idx + 24, 0, len(h1_t) - 1)
    df["px"] = h1_t.iloc[idx].values
    df["fwd_ret"] = h1_t.iloc[fwd_idx].values / df["px"] - 1

    # ── Distribución global ──
    print("\n── Distribución del bias (por cierre H1) ──")
    print(df["bias"].value_counts().to_string())
    print("\n── HTF trend (veto counter-trend) ──")
    print(df["htf_trend"].value_counts().to_string())

    # ── Acierto del bias vs forward return ──
    print("\n── ¿Acierta el bias? (retorno forward 24 H1) ──")
    for b, sign in (("BULLISH", 1), ("BEARISH", -1)):
        sub = df[df["bias"] == b]
        if len(sub):
            hit = (np.sign(sub["fwd_ret"]) == sign).mean()
            print(f"  {b:<8} n={len(sub):>4}  acierto={hit:.1%}  "
                  f"fwd_ret medio={sub['fwd_ret'].mean() * 100:+.2f}%")

    # ── Por día: bias dominante vs cambio real del oro ──
    print("\n── Por día: bias dominante vs oro real ──")
    print(f"  {'día':<12} {'BULL':>5} {'BEAR':>5} {'NEUT':>5} {'dominante':>10} "
          f"{'Δoro día':>9}  match")
    daily = []
    for d, sub in df.groupby("date_utc"):
        counts = sub["bias"].value_counts()
        bull, bear = int(counts.get("BULLISH", 0)), int(counts.get("BEARISH", 0))
        neut = int(counts.get("NEUTRAL", 0))
        dom = counts.idxmax()
        day_px = sub.sort_values("t_server")["px"]
        d_ret = (day_px.iloc[-1] / day_px.iloc[0] - 1) * 100 if len(day_px) > 1 else 0.0
        match = ("✓" if (dom == "BULLISH" and d_ret > 0)
                 or (dom == "BEARISH" and d_ret < 0) else
                 ("·" if dom == "NEUTRAL" else "✗"))
        print(f"  {str(d):<12} {bull:>5} {bear:>5} {neut:>5} {dom:>10} "
              f"{d_ret:>8.2f}%  {match}")
        daily.append({"date": str(d), "bull": bull, "bear": bear, "neut": neut,
                      "dominant": dom, "day_ret_pct": round(d_ret, 3),
                      "match": match})

    # ── Neto de la ventana ──
    px0, px1 = df["px"].iloc[0], df["px"].iloc[-1]
    net = (px1 / px0 - 1) * 100
    n_bear = (df["bias"] == "BEARISH").sum()
    n_bull = (df["bias"] == "BULLISH").sum()
    print(f"\nOro en la ventana: {px0:.0f} → {px1:.0f} ({net:+.2f}%)")
    print(f"Ratio BEARISH:BULLISH del bias = {n_bear}:{n_bull}")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = os.path.join(RESULTS_DIR,
                       f"bias_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"from": args.t_from, "to": args.t_to,
                   "net_gold_pct": round(net, 3),
                   "bias_counts": df["bias"].value_counts().to_dict(),
                   "htf_counts": df["htf_trend"].value_counts().to_dict(),
                   "daily": daily}, f, indent=1, ensure_ascii=False, default=str)
    print(f"Guardado: {out}")


if __name__ == "__main__":
    main()

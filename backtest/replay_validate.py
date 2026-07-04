"""
replay_validate — Meta-validación del backtester unificado contra el VIVO.

Reproduce jun–jul 2026 con el engine offline (modo candidatos, umbral 0) y
compara con las señales REALES de trades.db (stream OB intradía, deduplicadas
por vela). Tres verdictos:

  1. MATCH RATE — ¿el offline reproduce las señales reales? (objetivo ≥70-80%,
     y el 100% de los no-match con causa asignable)
  2. COMPONENTES — en los matches, los campos reproducibles (bias_h4, ob_type,
     regime, adx±2, sweep/fvg/m15, atr±5%) deben coincidir ≥95%.
     Desviación aquí = BUG (lookahead/TZ/forming-bar), NO efecto de mocks.
  3. GAP DE CONFLUENCIAS — Δ̄/σ de (conf_real − conf_offline), descompuesto con
     vp_score/delta_score/ml/news de la DB. Δ̄ es el traductor de escala
     offline→vivo para los barridos de umbral.

Uso (sin MT5, bot vivo OK):
    python backtest/replay_validate.py                  # ventana por defecto jun-jul
    python backtest/replay_validate.py --from 2026-06-03 --to 2026-07-03
"""

import os
import sys
import json
import sqlite3
import argparse
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.unified_backtester import run_unified, RESULTS_DIR

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "logs", "trades.db")

MATCH_WINDOW_MIN  = 75    # |Δt| máximo señal real vs offline
SHADOW_WINDOW_MIN = 360   # ventana extendida: dedup del engine tapó la re-emisión
ENTRY_TOL_ATR     = 0.5


def load_real_signals(t_from: str, t_to: str) -> pd.DataFrame:
    """Señales reales del stream OB, deduplicadas por vela H1 (fix spam)."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """SELECT timestamp, direction, entry, sl, tp1, tp2, rr, confluences,
                  bias_h4, ob_type, regime, adx, rsi_state, sweep_score,
                  fvg_score, m15_aligned, pairs_score, atr, vp_score,
                  delta_score, ml_proba, news_blackout, inter_score, outcome
           FROM signals
           WHERE model='OB' AND symbol='XAUUSD'
             AND timestamp >= ? AND timestamp <= ?
           ORDER BY timestamp ASC""",
        conn, params=(t_from, t_to))
    conn.close()
    if df.empty:
        return df
    ts = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df["ts_utc"] = ts.dt.tz_localize(None)
    # Dedup por vela|dirección|entry~0.1 (misma clave que learning_engine)
    key = (ts.dt.strftime("%Y-%m-%dT%H") + "|" + df["direction"].astype(str)
           + "|" + df["entry"].round(1).astype(str))
    return df.loc[~key.duplicated(keep="first")].reset_index(drop=True)


def collapse_to_ideas(real: pd.DataFrame) -> pd.DataFrame:
    """Colapsa las señales reales a IDEAS con la MISMA regla que el dedup del
    engine (_is_duplicate): misma dirección + entry a <0.75 ATR de la última
    = re-emisión de la misma idea (el vivo evalúa cada 60 s y re-emite cuando
    el entry deriva; el offline evalúa solo al cierre de vela)."""
    ideas, last = [], None
    for _, r in real.iterrows():
        atr = float(r["atr"] or 5.0)
        is_new = (last is None
                  or r["direction"] != last["direction"]
                  or abs(float(r["entry"]) - float(last["entry"])) >= 0.75 * atr)
        if is_new:
            ideas.append(r)
        last = r
    return pd.DataFrame(ideas).reset_index(drop=True)


def match_signals(real: pd.DataFrame, offline: list) -> list:
    """Para cada señal real busca el candidato offline más cercano en tiempo
    con misma dirección y entry dentro de ±0.5 ATR."""
    off = pd.DataFrame(offline)
    if off.empty:
        return [{"real_idx": i, "match": None, "cause": "sin candidatos offline"}
                for i in range(len(real))]
    off["ts_utc"] = pd.to_datetime(off["signal_time_utc"], utc=True).dt.tz_localize(None)

    results = []
    for i, r in real.iterrows():
        cand = off[off["direction"] == r["direction"]].copy()
        cand["dt_min"] = (cand["ts_utc"] - r["ts_utc"]).abs().dt.total_seconds() / 60
        atr_tol = ENTRY_TOL_ATR * float(r["atr"] or 5.0)
        near = cand[(cand["dt_min"] <= MATCH_WINDOW_MIN)
                    & ((cand["entry"] - float(r["entry"])).abs() <= atr_tol)]
        if len(near):
            results.append({"real_idx": i, "match": near.sort_values("dt_min").iloc[0],
                            "cause": None})
            continue
        # Causas asignables para el no-match
        shadow = cand[(cand["dt_min"] <= SHADOW_WINDOW_MIN)
                      & ((cand["entry"] - float(r["entry"])).abs() <= 2 * atr_tol)]
        if len(shadow):
            cause = "dedup del engine tapó la re-emisión (misma idea emitida antes offline)"
        elif len(cand[cand["dt_min"] <= MATCH_WINDOW_MIN]):
            cause = "candidato en ventana pero entry difiere >0.5 ATR"
        else:
            cause = "sin candidato offline en ±75 min (gate estructural divergió)"
        results.append({"real_idx": i, "match": None, "cause": cause})
    return results


def compare_components(r: pd.Series, o: pd.Series) -> dict:
    """Campos reproducibles — deben coincidir; desviación = bug."""
    out = {}
    out["bias_h4"] = str(r["bias_h4"]) == str(o["bias_h4"])
    out["ob_type"] = str(r["ob_type"]) == str(o["ob_type"])
    out["regime"]  = str(r["regime"]) == str(o["regime"])
    try:
        out["adx±2"] = abs(float(r["adx"]) - float(o["regime_adx"])) <= 2.0
    except (TypeError, ValueError):
        out["adx±2"] = None
    def _f(x):
        return 0.0 if x is None or pd.isna(x) else float(x)
    out["sweep"] = _f(r["sweep_score"]) == _f(o["sweep_score"])
    out["fvg"]   = _f(r["fvg_score"]) == _f(o["fvg_score"])
    out["m15"]   = int(_f(r["m15_aligned"])) == int(_f(o["m15_aligned"]))
    try:
        ra, oa = float(r["atr"]), float(o["atr"])
        out["atr±5%"] = abs(ra - oa) / ra <= 0.05 if ra else None
    except (TypeError, ValueError):
        out["atr±5%"] = None
    return out


def run(t_from: str, t_to: str, offline_json: str | None = None) -> dict:
    raw = load_real_signals(t_from, t_to + "T23:59:59")
    real = collapse_to_ideas(raw)
    print(f"Señales reales OB: {len(raw)} deduplicadas → {len(real)} ideas "
          f"(regla _is_duplicate del engine)")
    if real.empty:
        print("Sin señales reales — nada que validar")
        return {}

    if offline_json:
        res = json.load(open(offline_json, encoding="utf-8"))
        offline = res["signals_log"]
        print(f"Candidatos offline cargados de {offline_json}: {len(offline)}")
    else:
        print("Corriendo replay offline (candidatos, umbral 0, cadencia M15)...")
        res = run_unified(t_from=t_from, t_to=t_to, min_confluences=0.0,
                          candidates=True, record_discards=True, quiet=False,
                          cadence="M15")
        offline = res["signals_log"]
    discard_log = res.get("discard_log", [])

    matches = match_signals(real, offline)
    matched = [m for m in matches if m["match"] is not None]
    rate = len(matched) / len(real)

    # ── Componentes reproducibles ──
    comp_stats: dict = {}
    for m in matched:
        comp = compare_components(real.iloc[m["real_idx"]], m["match"])
        for k, v in comp.items():
            if v is None:
                continue
            ok_n, tot = comp_stats.get(k, (0, 0))
            comp_stats[k] = (ok_n + (1 if v else 0), tot + 1)

    # ── Gap de confluencias (traductor offline→vivo) ──
    gaps, decomp = [], {"vp": [], "delta": [], "ml": [], "news": [], "residual": []}
    for m in matched:
        r, o = real.iloc[m["real_idx"]], m["match"]
        gap = float(r["confluences"]) - float(o["confluences"])
        gaps.append(gap)
        vp    = float(r["vp_score"] or 0)
        delta = float(r["delta_score"] or 0)
        ml    = 1.0 if float(r["ml_proba"] or 0.5) >= 0.60 else 0.0
        news  = -1.0 if int(r["news_blackout"] or 0) else 0.0  # mock siempre +1
        decomp["vp"].append(vp);     decomp["delta"].append(delta)
        decomp["ml"].append(ml);     decomp["news"].append(news)
        decomp["residual"].append(gap - vp - delta - ml - news)

    # Causa fina para los no-match: qué descartó el engine offline en las
    # barras alrededor del instante de la señal real (100% asignable)
    dlog = pd.DataFrame(discard_log)
    offset_h = float(res.get("utc_offset", 3))
    if not dlog.empty:
        dlog["ts_utc"] = pd.to_datetime(dlog["t"]) - pd.Timedelta(hours=offset_h)

    causes: dict = {}
    for m in matches:
        if not m["cause"]:
            continue
        cause = m["cause"]
        if "gate estructural" in cause and not dlog.empty:
            r = real.iloc[m["real_idx"]]
            near = dlog[(dlog["ts_utc"] - r["ts_utc"]).abs()
                        <= pd.Timedelta(minutes=MATCH_WINDOW_MIN)]
            if len(near):
                top = near["reason"].value_counts().index[0]
                cause = f"gate offline: {top}"
        causes[cause] = causes.get(cause, 0) + 1

    print("\n" + "=" * 62)
    print(f"MATCH RATE: {len(matched)}/{len(real)} = {rate:.0%}  (objetivo ≥70%)")
    if causes:
        print("No-match por causa:")
        for c, n in sorted(causes.items(), key=lambda x: -x[1]):
            print(f"  {n:3d}  {c}")
    print("\nComponentes reproducibles (objetivo ≥95% cada uno):")
    comp_report = {}
    for k, (ok_n, tot) in comp_stats.items():
        pct = ok_n / tot if tot else 0
        comp_report[k] = round(pct, 3)
        flag = "OK " if pct >= 0.95 else ("~  " if pct >= 0.85 else "BUG?")
        print(f"  {flag} {k:10s} {ok_n}/{tot} = {pct:.0%}")
    if gaps:
        g = np.array(gaps)
        resid = np.array(decomp["residual"])
        print(f"\nGAP de confluencias (real − offline): Δ̄ = {g.mean():+.2f}  σ = {g.std():.2f}")
        print(f"  descomposición media: VP {np.mean(decomp['vp']):+.2f} | "
              f"delta {np.mean(decomp['delta']):+.2f} | ML {np.mean(decomp['ml']):+.2f} | "
              f"news {np.mean(decomp['news']):+.2f} | residual (macro+inter+ruido) "
              f"{resid.mean():+.2f} (σ {resid.std():.2f})")
        print(f"\n→ Traducción de umbral: umbral_vivo ≈ umbral_offline + {g.mean():.1f}")

    report = {
        "window": [t_from, t_to],
        "real_signals": len(real),
        "offline_candidates": len(offline),
        "match_rate": round(rate, 3),
        "unmatched_causes": causes,
        "components": comp_report,
        "gap_mean": round(float(np.mean(gaps)), 3) if gaps else None,
        "gap_std":  round(float(np.std(gaps)), 3) if gaps else None,
        "gap_decomposition": {k: round(float(np.mean(v)), 3) for k, v in decomp.items() if v},
        "created": datetime.now().isoformat(),
    }
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = os.path.join(RESULTS_DIR, "replay_validation.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nInforme → {out}")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="t_from", default="2026-06-03")
    ap.add_argument("--to", dest="t_to", default="2026-07-03")
    ap.add_argument("--offline-json", default=None,
                    help="reusar un results JSON en vez de correr el replay")
    args = ap.parse_args()
    run(args.t_from, args.t_to, args.offline_json)

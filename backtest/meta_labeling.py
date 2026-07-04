"""
Meta-labeling (López de Prado) — prototipo de evaluación HONESTA, read-only.

Idea: el SMC ya decide la DIRECCIÓN (modelo primario). Un modelo secundario
binario solo decide "¿tomo esta señal o la salto?" (gatekeeper). Se evalúa con
walk-forward temporal si ese filtro mejora la EXPECTANCIA (R por trade) fuera de
muestra respecto a tomar todas las señales.

Datos: trades en vivo (signals, outcome WIN/LOSS, R = pnl_pct) + muestras de
backtest como entrenamiento (peso menor). Replica la receta del LearningEngine.

OJO: los 250 trades vivos son de ~3 semanas (regime change). Con tan poca
historia multi-régimen, cualquier resultado positivo es sospechoso de fitting.
Este script existe para CUANTIFICAR eso, no para activar nada.

Uso:  PY backtest/meta_labeling.py
"""

import os
import sys
import sqlite3

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
from sklearn.ensemble import (
    RandomForestClassifier, GradientBoostingClassifier, ExtraTreesClassifier,
)
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit

from ml.learning_engine import LearningEngine
from ml.training_store import load_samples

DB = "logs/trades.db"
LIVE_W, BT_W = 3.0, 1.0


def _models():
    return {
        "rf": RandomForestClassifier(n_estimators=200, max_depth=5, min_samples_leaf=3,
                                     class_weight="balanced", random_state=42, n_jobs=-1),
        "gb": GradientBoostingClassifier(n_estimators=100, learning_rate=0.05, max_depth=3,
                                         min_samples_leaf=4, random_state=42),
        "et": ExtraTreesClassifier(n_estimators=150, max_depth=5, min_samples_leaf=3,
                                   class_weight="balanced", random_state=42, n_jobs=-1),
        "mlp": make_pipeline(StandardScaler(), MLPClassifier(
            hidden_layer_sizes=(32, 16), alpha=1e-3, max_iter=600,
            early_stopping=True, n_iter_no_change=15, random_state=42)),
    }


def _ensemble_proba(X, y, w, Xt):
    ps = []
    for clf in _models().values():
        try:
            clf.fit(X, y, sample_weight=w)
        except Exception:
            clf.fit(X, y)
        ps.append(clf.predict_proba(Xt)[:, 1])
    return np.mean(ps, axis=0)


def _dedup_live(live: list) -> list:
    """Colapsa trades duplicados (mismo bug de spam 2026-07): misma vela H1 +
    modelo + dirección + entry~0.1. Un setup repetido 10× sesga el meta-modelo
    (le enseña esa pérdida/ganancia 10 veces). Se queda el primero de cada grupo."""
    from datetime import datetime
    seen, out = set(), []
    for d in live:
        try:
            bar = datetime.fromisoformat(str(d["timestamp"])).strftime("%Y-%m-%dT%H")
        except Exception:
            bar = str(d.get("timestamp"))
        key = (bar, d.get("model", "OB"), d.get("direction"),
               round(float(d.get("entry", 0)), 1))
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def main():
    dedup = "--dedup" in sys.argv
    eng = LearningEngine(DB)
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT * FROM signals WHERE outcome IN ('WIN','LOSS') ORDER BY timestamp ASC", conn)
    conn.close()
    live = [r.to_dict() for _, r in df.iterrows()]
    if dedup:
        n0 = len(live)
        live = _dedup_live(live)
        print(f"[--dedup] {n0} → {len(live)} trades ({n0-len(live)} duplicados eliminados)\n")
    for d in live:
        d["source"] = 1
    Xl = np.array([eng._extract_features(d) for d in live], float)
    yl = np.array([1 if d["outcome"] == "WIN" else 0 for d in live])
    # R realizado por trade: pnl_pct (WIN=+rr, LOSS=-1)
    R = np.array([float(d.get("pnl_pct") if d.get("pnl_pct") is not None
                        else (d.get("rr", 2.0) if d["outcome"] == "WIN" else -1.0))
                  for d in live])

    bt = list(load_samples(DB, source="backtest"))
    for d in bt:
        d.setdefault("source", 0)
    Xb = np.array([eng._extract_features(d) for d in bt], float)
    yb = np.array([1 if d.get("outcome") == "WIN" else 0 for d in bt])

    print(f"Live: {len(yl)} trades (WR {yl.mean():.1%}, exp {R.mean():+.3f}R) | "
          f"Backtest train: {len(yb)}\n")

    # Walk-forward: proba OOS para cada trade vivo
    proba = np.full(len(yl), np.nan)
    tss = TimeSeriesSplit(n_splits=5)
    for tr, te in tss.split(Xl):
        X = np.vstack([Xb, Xl[tr]]); y = np.concatenate([yb, yl[tr]])
        w = np.concatenate([np.full(len(yb), BT_W), np.full(len(tr), LIVE_W)])
        proba[te] = _ensemble_proba(X, y, w, Xl[te])
    m = ~np.isnan(proba)
    p, yv, Rv = proba[m], yl[m], R[m]
    n = len(p)
    print(f"== Walk-forward OOS sobre {n} trades vivos ==")
    print(f"  BASELINE (tomar todas):  n={n}  WR={yv.mean():.1%}  exp={Rv.mean():+.3f}R")

    med = np.median(p)
    hi = p >= med
    lo = p < med
    print(f"  Gate proba ALTA (>=med): n={hi.sum()}  WR={yv[hi].mean():.1%}  exp={Rv[hi].mean():+.3f}R")
    print(f"  Gate proba BAJA (<med):  n={lo.sum()}  WR={yv[lo].mean():.1%}  exp={Rv[lo].mean():+.3f}R")

    # Filtro meta con umbral elegido en train (walk-forward anidado simplificado):
    # para cada fold, umbral que maximiza expectancia en train, aplicado a test.
    print("\n== Meta-filtro con umbral auto (train→test) ==")
    kept_R, kept_y, taken = [], [], 0
    for tr, te in tss.split(Xl):
        X = np.vstack([Xb, Xl[tr]]); y = np.concatenate([yb, yl[tr]])
        w = np.concatenate([np.full(len(yb), BT_W), np.full(len(tr), LIVE_W)])
        ptr = _ensemble_proba(X, y, w, Xl[tr])
        pte = _ensemble_proba(X, y, w, Xl[te])
        Rtr = R[tr]
        best_thr, best_exp = None, -1e9
        for thr in np.quantile(ptr, np.linspace(0.1, 0.9, 17)):
            sel = ptr >= thr
            if sel.sum() >= max(5, len(tr)//5):
                e = Rtr[sel].mean()
                if e > best_exp:
                    best_exp, best_thr = e, thr
        if best_thr is None:
            best_thr = np.median(ptr)
        sel = pte >= best_thr
        taken += int(sel.sum())
        kept_R.extend(R[te][sel].tolist())
        kept_y.extend(yl[te][sel].tolist())
    kept_R = np.array(kept_R); kept_y = np.array(kept_y)
    if len(kept_R):
        print(f"  Tras meta-filtro: n={len(kept_R)} ({len(kept_R)/n:.0%} de las señales)  "
              f"WR={kept_y.mean():.1%}  exp={kept_R.mean():+.3f}R")
    else:
        print("  El meta-filtro no dejó pasar señales.")

    print("\n" + "=" * 60)
    base = Rv.mean()
    meta = kept_R.mean() if len(kept_R) else -1e9
    if meta > base + 0.05 and len(kept_R) >= n * 0.4:
        print(f"✅ Meta-labeling mejora la expectancia OOS ({base:+.3f}→{meta:+.3f}R). "
              f"Candidato a integrar (con más datos para confirmar).")
    else:
        print(f"🔴 Meta-labeling NO mejora robustamente (base {base:+.3f}R vs "
              f"meta {meta:+.3f}R). Esperado: 3 semanas de datos = no-estacionariedad.")
        print("   Conclusión: no integrar aún; necesita historia multi-régimen.")


if __name__ == "__main__":
    main()

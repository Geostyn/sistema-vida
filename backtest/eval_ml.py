"""
Evaluación HONESTA del edge del ML (ensemble) — read-only, no toca el modelo vivo.

Por qué existe:
  - `diagnose.py` midió ml_proba ALMACENADA (r=-0.267) sobre pocas señales y con
    features que en su día iban en cero → poco fiable.
  - El `train()` del LearningEngine mide su accuracy con `cross_val_score` de folds
    BARAJADOS (StratifiedKFold). Con trades temporalmente agrupados eso FILTRA
    (trades vecinos caen en train y test) → AUC ~0.86 ENGAÑOSO.
  - La verdad de despliegue es WALK-FORWARD: entrenar con el pasado, predecir el
    futuro. Eso es lo que mide este script.

Hallazgo 2026-06-19 (250 trades vivos + 83 backtest):
  - Shuffle (engañoso): AUC 0.859, r +0.591
  - Walk-forward (honesto): AUC 0.379, r -0.145  → ML INVERTIDO out-of-sample.
  - Cuartil de proba baja → WR 75%, proba alta → WR 48% (predice al revés).
  - Causa: NO-ESTACIONARIEDAD (el patrón aprendido se invierte al cambiar el régimen).

Regla: reactivar `ml.vote_enabled` SOLO si el walk-forward da AUC >= 0.55 y r > 0
sobre trades POST-fix (no sobre la historia sesgada de SELL-en-subida).

Uso:  PY backtest/eval_ml.py
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
from sklearn.model_selection import TimeSeriesSplit, StratifiedKFold
from sklearn.metrics import roc_auc_score

from ml.learning_engine import LearningEngine, FEATURE_NAMES
from ml.training_store import load_samples

DB = "logs/trades.db"
LIVE_WEIGHT, BACKTEST_WEIGHT = 3.0, 1.0


def _models():
    """Mismos candidatos e hiperparámetros que LearningEngine.train()."""
    return {
        "rf": RandomForestClassifier(
            n_estimators=200, max_depth=5, min_samples_leaf=3,
            class_weight="balanced", random_state=42, n_jobs=-1),
        "gb": GradientBoostingClassifier(
            n_estimators=100, learning_rate=0.05, max_depth=3,
            min_samples_leaf=4, random_state=42),
        "et": ExtraTreesClassifier(
            n_estimators=150, max_depth=5, min_samples_leaf=3,
            class_weight="balanced", random_state=42, n_jobs=-1),
        "mlp": make_pipeline(StandardScaler(), MLPClassifier(
            hidden_layer_sizes=(32, 16), alpha=1e-3, max_iter=600,
            early_stopping=True, n_iter_no_change=15, random_state=42)),
    }


def _load():
    eng = LearningEngine(DB)
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT * FROM signals WHERE outcome IN ('WIN','LOSS') ORDER BY timestamp ASC",
        conn)
    conn.close()
    live = [r.to_dict() for _, r in df.iterrows()]
    for d in live:
        d["source"] = 1
    Xl = np.array([eng._extract_features(d) for d in live], float)
    yl = np.array([1 if d["outcome"] == "WIN" else 0 for d in live])

    bt = list(load_samples(DB, source="backtest"))
    for d in bt:
        d.setdefault("source", 0)
    Xb = (np.array([eng._extract_features(d) for d in bt], float)
          if bt else np.empty((0, Xl.shape[1])))
    yb = (np.array([1 if d.get("outcome") == "WIN" else 0 for d in bt])
          if bt else np.empty((0,), int))
    return Xl, yl, Xb, yb


def _oof(Xl, yl, Xb, yb, splitter):
    """Probas out-of-fold del ensemble con el splitter dado."""
    oof = np.full(len(yl), np.nan)
    for tr, te in splitter.split(Xl, yl):
        X = np.vstack([Xb, Xl[tr]]) if len(yb) else Xl[tr]
        y = np.concatenate([yb, yl[tr]]) if len(yb) else yl[tr]
        w = (np.concatenate([np.full(len(yb), BACKTEST_WEIGHT),
                             np.full(len(tr), LIVE_WEIGHT)])
             if len(yb) else np.full(len(tr), LIVE_WEIGHT))
        ps = []
        for clf in _models().values():
            try:
                clf.fit(X, y, sample_weight=w)
            except Exception:
                clf.fit(X, y)
            ps.append(clf.predict_proba(Xl[te])[:, 1])
        oof[te] = np.mean(ps, axis=0)
    return oof


def _report(tag, y, p):
    auc = roc_auc_score(y, p)
    r = np.corrcoef(y, p)[0, 1]
    print(f"  {tag:<24} AUC={auc:.3f}  r(proba,WIN)={r:+.3f}")
    return auc, r


def main():
    Xl, yl, Xb, yb = _load()
    print(f"Live: {len(yl)} trades (WR {yl.mean():.1%}) | "
          f"Backtest: {len(yb)} (WR {yb.mean() if len(yb) else 0:.1%})\n")

    # 1) Shuffle (lo que hace train() hoy) — para mostrar el espejismo
    sh = _oof(Xl, yl, Xb, yb, StratifiedKFold(5, shuffle=True, random_state=42))
    _report("Shuffle (ENGAÑOSO)", yl, sh)

    # 2) Walk-forward temporal — la verdad de despliegue
    wf = _oof(Xl, yl, Xb, yb, TimeSeriesSplit(n_splits=5))
    mask = ~np.isnan(wf)
    yv, pv = yl[mask], wf[mask]
    auc, r = _report("Walk-forward (HONESTO)", yv, pv)

    print(f"\n  WR por cuartil de proba (walk-forward, {mask.sum()} trades):")
    q = np.asarray(pd.qcut(pv, 4, labels=["Q1 baja", "Q2", "Q3", "Q4 alta"],
                           duplicates="drop"))
    for lab in ["Q1 baja", "Q2", "Q3", "Q4 alta"]:
        m = q == lab
        if m.sum():
            print(f"    {lab:<8} n={m.sum():>3}  WR={yv[m].mean():.1%}  "
                  f"proba~{pv[m].mean():.2f}")

    ok = auc >= 0.55 and r > 0
    print("\n" + "=" * 60)
    if ok:
        print(f"✅ ML con edge real (AUC {auc:.2f}, r {r:+.2f}) → puede volver a votar.")
        print("   Poner ml.vote_enabled: true en config.yaml.")
    else:
        print(f"🔴 ML SIN edge forward (AUC {auc:.2f}, r {r:+.2f}). Mantener "
              f"ml.vote_enabled: false.")
        print("   Causa probable: no-estacionariedad (el patrón se invierte al "
              "cambiar el régimen).")
        print("   Fix correcto: meta-labeling / modelo condicionado a régimen, "
              "no reentrenar sobre la misma historia.")


if __name__ == "__main__":
    main()

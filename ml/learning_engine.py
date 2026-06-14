"""
Learning Engine — Ensemble de 3 modelos ML + Bayesian Confluence Weights.

Modelos (todos en sklearn, sin instalaciones extra):
  - RandomForestClassifier     (robusto, maneja bien features con ruido)
  - GradientBoostingClassifier (aprende errores del RF)
  - ExtraTreesClassifier       (alta varianza controlada)

Votación: media ponderada por CV accuracy de cada modelo.
Signal válida si ensemble_proba >= 0.60 (antes: RF single >= 0.65).

Features expandidas: 9 → 16
  hour_utc, day_of_week, session, direction,
  confluences, confidence, news_blackout,
  rsi_state, ob_type, bias_h4,
  vp_score, delta_score, atr_pct, hurst, adx, pairs_score

Bayesian Weights: cuando hay >= 30 trades, calcula WR histórico por confluencia
y guarda en logs/bayesian_weights.json para ajustar confluencias dinámicamente.
"""

import sqlite3
import json
import os
import logging
import pickle
from datetime import datetime, timezone

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MIN_TRADES_TO_TRAIN  = 20
RETRAIN_EVERY        = 10
MODEL_PATH           = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "ml_model.pkl")
STATS_PATH           = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "ml_stats.json")
BAYESIAN_PATH        = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "bayesian_weights.json")

FEATURE_NAMES = [
    "direction", "session", "hour_utc", "day_of_week",
    "confluences", "confidence", "news_blackout",
    "rsi_numeric", "ob_type", "bias_h4",
    "vp_score", "delta_score", "atr_pct",
    "hurst", "adx", "pairs_score",
    "sweep_score", "fvg_score", "m15_aligned",
    "htf_liq_dist",   # distancia (ATR) a liquidez HTF en contra; 99 = ninguna
]


def _f(row, key, default=0.0) -> float:
    """float() seguro: maneja None/NaN de columnas DB antiguas."""
    try:
        v = row.get(key, default)
        if v is None:
            return float(default)
        v = float(v)
        return float(default) if v != v else v  # NaN check
    except Exception:
        return float(default)


class LearningEngine:
    def __init__(self, db_path: str):
        self.db_path     = db_path
        self.models: dict = {}   # {name: model}
        self.weights: dict = {}  # {name: cv_accuracy}
        self.last_count  = 0
        self._load_model()

    # ── Persistencia ───────────────────────────────────────────────

    def _load_model(self):
        if os.path.exists(MODEL_PATH):
            try:
                with open(MODEL_PATH, "rb") as f:
                    saved = pickle.load(f)
                    self.models      = saved.get("models", {})
                    self.weights     = saved.get("weights", {})
                    self.last_count  = saved.get("trained_on", 0)
                logger.info(
                    f"Ensemble ML cargado ({len(self.models)} modelos, "
                    f"entrenado con {self.last_count} trades)"
                )
            except Exception as e:
                logger.warning(f"No se pudo cargar el modelo ML: {e}")

    def _save_model(self, n_trades: int):
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump({
                "models":     self.models,
                "weights":    self.weights,
                "trained_on": n_trades,
            }, f)

    # ── Features ────────────────────────────────────────────────────

    def _extract_features(self, row) -> list:
        """Extrae 16 features de una señal (dict o fila de DB)."""
        # Hora y sesión
        try:
            ts   = datetime.fromisoformat(str(row.get("timestamp", "")))
            hour = ts.hour
            dow  = ts.weekday()  # 0=lunes
        except Exception:
            hour = 12
            dow  = 2

        if 7 <= hour < 12:
            session = 1   # Londres
        elif 12 <= hour < 17:
            session = 3   # Overlap
        elif 17 <= hour < 21:
            session = 2   # NY
        else:
            session = 0   # Asia

        direction     = 1 if str(row.get("direction", "")) == "BUY" else 0
        confluences   = float(row.get("confluences", 0))
        confidence    = float(row.get("confidence", 0))
        news_blackout = int(row.get("news_blackout", 0))

        rsi_map    = {"OVERSOLD": -1, "NEUTRAL": 0, "OVERBOUGHT": 1}
        rsi_num    = rsi_map.get(str(row.get("rsi_state", "NEUTRAL")), 0)

        ob_type    = 1 if str(row.get("ob_type", "")) == "BULLISH" else 0
        bias_map   = {"BULLISH": 1, "NEUTRAL": 0, "BEARISH": -1}
        bias_h4    = bias_map.get(str(row.get("bias_h4", "NEUTRAL")), 0)

        # Nuevas features (pueden ser NULL en DB antigua → default seguro)
        vp_score    = _f(row, "vp_score", 0)
        delta_score = _f(row, "delta_score", 0)
        atr_pct     = _f(row, "atr_pct", 0)
        hurst       = _f(row, "hurst", 0.5)
        adx         = _f(row, "adx", 20)
        pairs_score = _f(row, "pairs_score", 0)
        sweep_score = _f(row, "sweep_score", 0)
        fvg_score   = _f(row, "fvg_score", 0)
        m15_aligned = _f(row, "m15_aligned", 0)
        # 99 = sin pool HTF en contra (señales antiguas sin la columna → 99)
        htf_liq     = _f(row, "htf_liq_dist", 99)

        return [
            direction, session, hour, dow,
            confluences, confidence, news_blackout,
            rsi_num, ob_type, bias_h4,
            vp_score, delta_score, atr_pct,
            hurst, adx, pairs_score,
            sweep_score, fvg_score, m15_aligned,
            htf_liq,
        ]

    # ── Datos de entrenamiento ─────────────────────────────────────

    def _load_training_data(self):
        try:
            conn = sqlite3.connect(self.db_path)
            df   = pd.read_sql_query("""
                SELECT *
                FROM signals
                WHERE outcome IN ('WIN', 'LOSS')
                ORDER BY timestamp ASC
            """, conn)
            conn.close()
        except Exception as e:
            logger.warning(f"Error leyendo datos de entrenamiento: {e}")
            return None, None

        if len(df) < MIN_TRADES_TO_TRAIN:
            return None, None

        X = np.array([self._extract_features(row) for _, row in df.iterrows()], dtype=float)
        y = np.array([1 if r == "WIN" else 0 for r in df["outcome"]])
        return X, y

    # ── Entrenamiento Ensemble ─────────────────────────────────────

    def train(self, force: bool = False) -> dict:
        from sklearn.ensemble import (
            RandomForestClassifier,
            GradientBoostingClassifier,
            ExtraTreesClassifier,
        )
        from sklearn.model_selection import cross_val_score

        X, y = self._load_training_data()
        if X is None:
            return {"trained": False, "reason": f"Menos de {MIN_TRADES_TO_TRAIN} trades cerrados"}

        n_trades = len(y)
        if not force and n_trades < self.last_count + RETRAIN_EVERY:
            return {"trained": False, "reason": "No hay suficientes trades nuevos"}

        candidates = {
            "rf": RandomForestClassifier(
                n_estimators=200, max_depth=5, min_samples_leaf=3,
                class_weight="balanced", random_state=42, n_jobs=-1,
            ),
            "gb": GradientBoostingClassifier(
                n_estimators=100, learning_rate=0.05, max_depth=3,
                min_samples_leaf=4, random_state=42,
            ),
            "et": ExtraTreesClassifier(
                n_estimators=150, max_depth=5, min_samples_leaf=3,
                class_weight="balanced", random_state=42, n_jobs=-1,
            ),
        }

        cv_folds   = min(5, n_trades // 6) if n_trades >= 30 else 2
        new_models = {}
        new_weights = {}
        best_importance = None

        for name, clf in candidates.items():
            try:
                clf.fit(X, y)
                if n_trades >= cv_folds * 3:
                    scores = cross_val_score(clf, X, y, cv=cv_folds, scoring="accuracy")
                    acc    = float(scores.mean())
                else:
                    acc = float(np.mean(y == clf.predict(X)))
                new_models[name]  = clf
                new_weights[name] = acc
                logger.info(f"  [{name}] CV accuracy: {acc:.1%}")
                if name == "rf":
                    best_importance = clf.feature_importances_
            except Exception as e:
                logger.warning(f"  [{name}] falló: {e}")

        if not new_models:
            return {"trained": False, "reason": "Todos los modelos fallaron"}

        self.models     = new_models
        self.weights    = new_weights
        self.last_count = n_trades
        self._save_model(n_trades)

        # Estadísticas
        avg_acc = float(np.mean(list(new_weights.values()))) if new_weights else 0
        importance = {}
        if best_importance is not None:
            for name, imp in zip(FEATURE_NAMES[:len(best_importance)], best_importance):
                importance[name] = round(float(imp), 4)

        stats = {
            "trained":     True,
            "n_trades":    n_trades,
            "win_rate":    round(float(np.mean(y)), 3),
            "cv_accuracy": round(avg_acc, 3),
            "model_accs":  {k: round(v, 3) for k, v in new_weights.items()},
            "importance":  importance,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }

        os.makedirs(os.path.dirname(STATS_PATH), exist_ok=True)
        with open(STATS_PATH, "w") as f:
            json.dump(stats, f, indent=2)

        # Calcular Bayesian weights si hay suficientes datos
        if n_trades >= 30:
            self._compute_bayesian_weights(X, y)

        logger.info(
            f"Ensemble ML entrenado | {n_trades} trades | "
            f"WR real: {stats['win_rate']:.1%} | CV medio: {avg_acc:.1%} | "
            f"Modelos: {list(new_models.keys())}"
        )
        return stats

    def _compute_bayesian_weights(self, X: np.ndarray, y: np.ndarray):
        """
        Calcula la tasa de WIN histórica para subgrupos de features.
        Guarda en logs/bayesian_weights.json.
        """
        try:
            df = pd.DataFrame(X, columns=FEATURE_NAMES[:X.shape[1]])
            df["win"] = y

            weights = {}

            # DXY: no está directamente en features DB, usar pares_score
            for thresh, label in [(0.6, "pairs_score_high"), (0.3, "pairs_score_low")]:
                mask  = df["pairs_score"] >= thresh
                if mask.sum() >= 5:
                    weights[label] = round(float(df.loc[mask, "win"].mean()), 3)

            # VP score
            for thresh, label in [(1.0, "vp_score_high"), (0.5, "vp_score_med")]:
                mask = df["vp_score"] >= thresh
                if mask.sum() >= 5:
                    weights[label] = round(float(df.loc[mask, "win"].mean()), 3)

            # Sesión
            for sess_id, label in [(1, "session_london"), (3, "session_overlap"), (2, "session_ny")]:
                mask = df["session"] == sess_id
                if mask.sum() >= 5:
                    weights[label] = round(float(df.loc[mask, "win"].mean()), 3)

            # Hurst > 0.55 (trending)
            mask = df["hurst"] > 0.55
            if mask.sum() >= 5:
                weights["regime_trending"] = round(float(df.loc[mask, "win"].mean()), 3)

            # ADX > 25
            mask = df["adx"] > 25
            if mask.sum() >= 5:
                weights["adx_strong"] = round(float(df.loc[mask, "win"].mean()), 3)

            weights["computed_at"] = datetime.now(timezone.utc).isoformat()
            weights["n_trades"]    = int(len(y))

            os.makedirs(os.path.dirname(BAYESIAN_PATH), exist_ok=True)
            with open(BAYESIAN_PATH, "w") as f:
                json.dump(weights, f, indent=2)

            logger.info(f"Bayesian weights actualizados: {weights}")

        except Exception as e:
            logger.warning(f"Error calculando Bayesian weights: {e}")

    # ── Predicción ──────────────────────────────────────────────────

    def predict_win_probability(self, signal: dict) -> float:
        """
        Predicción con el ensemble. Media ponderada por CV accuracy.
        Si no hay modelos entrenados → 0.5 (neutral).
        """
        if not self.models:
            return 0.5

        try:
            features = np.array([self._extract_features(signal)], dtype=float)

            # Modelo antiguo entrenado con menos features → reentrenar ya
            first = next(iter(self.models.values()))
            n_expected = getattr(first, "n_features_in_", features.shape[1])
            if n_expected != features.shape[1]:
                logger.info(
                    f"Modelo ML con {n_expected} features pero señal tiene "
                    f"{features.shape[1]} — reentrenando con el set nuevo"
                )
                result = self.train(force=True)
                if not result.get("trained"):
                    return 0.5

            total_weight = sum(self.weights.values()) or 1.0
            proba_sum    = 0.0

            for name, clf in self.models.items():
                w    = self.weights.get(name, 1.0)
                prob = clf.predict_proba(features)
                p1   = float(prob[0][1]) if prob.shape[1] > 1 else 0.5
                proba_sum += p1 * w

            return float(np.clip(proba_sum / total_weight, 0.0, 1.0))

        except Exception as e:
            logger.warning(f"Error en predicción ensemble: {e}")
            return 0.5

    def get_bayesian_weights(self) -> dict:
        """Lee los Bayesian weights guardados."""
        if os.path.exists(BAYESIAN_PATH):
            try:
                with open(BAYESIAN_PATH) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def get_model_stats(self) -> dict:
        if os.path.exists(STATS_PATH):
            try:
                with open(STATS_PATH) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"trained": False, "n_trades": 0}

    def should_retrain(self) -> bool:
        X, y = self._load_training_data()
        if X is None:
            return False
        return len(y) >= self.last_count + RETRAIN_EVERY

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
    # Features intermarket (Fase 2, 2026-06-17) — NO derivan del precio del oro
    "inter_score",    # score combinado bias-oro [-1..+1] (reales+COT+riesgo)
    "real_yield_imp", # impacto reales 10Y (+1 reales bajan=oro alcista)
    "cot_impact",     # posicionamiento COT (extremos contrarian + momentum)
    "cot_percentile", # percentil net non-commercial (0..1)
    # Features quant (Fase 3) — defaults seguros hasta que los motores los pueblen
    "garch_vol",      # volatilidad prevista GARCH(1,1), 0 = sin dato
    "kalman_slope",   # pendiente de tendencia filtrada (Kalman), 0 = sin dato
    # Procedencia de la muestra (1 = vivo, 0 = backtest) — evita confundir
    # distribuciones cuando se entrena con vivo + backtest mezclados
    "source",
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

        # Intermarket (Fase 2) — default neutro si la fila no los tiene
        inter_score    = _f(row, "inter_score", 0)
        real_yield_imp = _f(row, "real_yield_imp", 0)
        cot_impact     = _f(row, "cot_impact", 0)
        cot_percentile = _f(row, "cot_percentile", 0.5)
        # Quant (Fase 3) — default 0 hasta que GARCH/Kalman los pueblen
        garch_vol      = _f(row, "garch_vol", 0)
        kalman_slope   = _f(row, "kalman_slope", 0)
        # Procedencia: 1 = vivo (default para predicción en vivo), 0 = backtest
        src_raw = row.get("source", 1)
        source  = 0.0 if (str(src_raw).lower().startswith("back") or src_raw in (0, "0", 0.0)) else 1.0

        return [
            direction, session, hour, dow,
            confluences, confidence, news_blackout,
            rsi_num, ob_type, bias_h4,
            vp_score, delta_score, atr_pct,
            hurst, adx, pairs_score,
            sweep_score, fvg_score, m15_aligned,
            htf_liq,
            inter_score, real_yield_imp, cot_impact, cot_percentile,
            garch_vol, kalman_slope,
            source,
        ]

    # ── Datos de entrenamiento ─────────────────────────────────────

    # Peso relativo de un trade en vivo vs uno de backtest. El vivo es más
    # realista (spread/slippage/fills reales) → pesa más. El backtest aporta
    # VOLUMEN (miles de muestras) para que el modelo aprenda rápido.
    LIVE_WEIGHT     = 3.0
    BACKTEST_WEIGHT = 1.0

    def _load_training_data(self, with_weights: bool = False):
        """
        Carga datos de entrenamiento: trades en vivo (tabla signals) + muestras
        de backtest (tabla training_samples). Devuelve (X, y) o (X, y, w).
        """
        rows = []          # cada item: (feature_dict, outcome, weight)

        # ── 1. Trades en vivo (tabla signals) ──────────────────────
        try:
            conn = sqlite3.connect(self.db_path)
            df = pd.read_sql_query("""
                SELECT * FROM signals
                WHERE outcome IN ('WIN', 'LOSS')
                ORDER BY timestamp ASC
            """, conn)
            conn.close()
            # Dedup de entrenamiento (fix spam 2026-07): antes ~24% de las filas
            # eran señales DUPLICADAS (mismo setup emitido 10× en una vela) → el
            # modelo aprendía ese resultado 10 veces. Se colapsan por
            # vela-H1 | modelo | dirección | entry~0.1, quedándose la primera.
            if not df.empty and "timestamp" in df.columns:
                _bar   = pd.to_datetime(df["timestamp"], errors="coerce", utc=True).dt.strftime("%Y-%m-%dT%H")
                _model = (df["model"] if "model" in df.columns else "OB")
                _key   = (_bar.astype(str) + "|" + pd.Series(_model, index=df.index).fillna("OB").astype(str)
                          + "|" + df["direction"].astype(str)
                          + "|" + df["entry"].round(1).astype(str))
                n0 = len(df)
                df = df.loc[~_key.duplicated(keep="first")].copy()
                if len(df) < n0:
                    logger.info(f"ML training: {n0-len(df)} señales duplicadas descartadas ({len(df)} limpias)")
            for _, r in df.iterrows():
                d = r.to_dict()
                d["source"] = 1  # vivo
                rows.append((d, d["outcome"], self.LIVE_WEIGHT))
        except Exception as e:
            logger.warning(f"Error leyendo signals (vivo): {e}")

        # ── 2. Muestras de backtest (tabla training_samples) ───────
        try:
            from ml.training_store import load_samples
            for s in load_samples(self.db_path, source="backtest"):
                rows.append((s, s.get("outcome"), self.BACKTEST_WEIGHT))
        except Exception as e:
            logger.debug(f"training_samples no disponible: {e}")

        if len(rows) < MIN_TRADES_TO_TRAIN:
            return (None, None, None) if with_weights else (None, None)

        # Orden temporal GLOBAL (vivo + backtest mezclados). Sin esto el CV
        # temporal no sirve: el backtest quedaba concatenado DESPUÉS del vivo
        # y los folds mezclaban pasado y futuro (fuga look-ahead).
        def _ts_key(d: dict):
            t = pd.to_datetime(d.get("timestamp"), errors="coerce", utc=True)
            return t if pd.notna(t) else pd.Timestamp.min.tz_localize("UTC")
        rows.sort(key=lambda item: _ts_key(item[0]))

        X = np.array([self._extract_features(d) for d, _, _ in rows], dtype=float)
        y = np.array([1 if o == "WIN" else 0 for _, o, _ in rows])
        w = np.array([wt for _, _, wt in rows], dtype=float)
        return (X, y, w) if with_weights else (X, y)

    # ── Entrenamiento Ensemble ─────────────────────────────────────

    def train(self, force: bool = False) -> dict:
        from sklearn.ensemble import (
            RandomForestClassifier,
            GradientBoostingClassifier,
            ExtraTreesClassifier,
        )
        from sklearn.neural_network import MLPClassifier
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.model_selection import cross_val_score, TimeSeriesSplit

        X, y, w = self._load_training_data(with_weights=True)
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
            # Red neuronal tabular (MLP) — escalada; ahora viable con los datos
            # de backtest. early_stopping + L2 (alpha) contra sobreajuste.
            "mlp": make_pipeline(
                StandardScaler(),
                MLPClassifier(
                    hidden_layer_sizes=(32, 16), alpha=1e-3, max_iter=600,
                    early_stopping=True, n_iter_no_change=15, random_state=42,
                ),
            ),
        }

        cv_folds   = min(5, n_trades // 6) if n_trades >= 30 else 2
        new_models = {}
        new_weights = {}
        best_importance = None

        for name, clf in candidates.items():
            try:
                # sample_weight solo lo soportan los modelos de árbol; el MLP
                # (Pipeline) no → reintento sin pesos ante cualquier fallo
                try:
                    clf.fit(X, y, sample_weight=w)
                except Exception:
                    clf.fit(X, y)
                if n_trades >= cv_folds * 3:
                    # CV temporal: entrena en pasado y valida en futuro.
                    # cv=int era StratifiedKFold → mezclaba futuro en train (fuga).
                    scores = cross_val_score(
                        clf, X, y, cv=TimeSeriesSplit(n_splits=cv_folds),
                        scoring="accuracy",
                    )
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

"""
Training Store — Muestras de entrenamiento de los BACKTESTS.

Idea (pedido del usuario 2026-06-17): el ML no debe aprender solo de los ~245
trades en vivo; los backtests generan MILES de trades etiquetados. Aquí se
guardan esas muestras (features + outcome) con source='backtest' para que
LearningEngine entrene con vivo + backtest y aprenda mucho más rápido.

Honestidad cuantitativa:
  - El backtest idealiza fills (sin slippage real) → se le da MENOS peso que al
    vivo (sample_weight) y la validación out-of-sample se hace solo sobre vivo.
  - Entrenar sobre "cuándo gana mi propia lógica" = meta-labeling (López de Prado).

Tabla `training_samples`:
  id, timestamp, source, outcome (WIN/LOSS), features_json
"""

import os
import json
import sqlite3
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def ensure_table(db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS training_samples (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT,
                source        TEXT DEFAULT 'backtest',
                outcome       TEXT,
                features_json TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()


def clear_source(db_path: str, source: str = "backtest"):
    """Borra muestras de una fuente (para regenerar el backtest sin duplicar)."""
    ensure_table(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM training_samples WHERE source = ?", (source,))
        conn.commit()
    finally:
        conn.close()


def record_samples(db_path: str, samples: list[dict], source: str = "backtest"):
    """
    Guarda una lista de muestras. Cada muestra: dict de features que DEBE incluir
    'outcome' ('WIN' o 'LOSS') y, si se puede, 'timestamp'.
    """
    if not samples:
        return 0
    ensure_table(db_path)
    conn = sqlite3.connect(db_path)
    n = 0
    try:
        for s in samples:
            outcome = s.get("outcome")
            if outcome not in ("WIN", "LOSS"):
                continue
            ts = s.get("timestamp") or datetime.now(timezone.utc).isoformat()
            feats = {k: v for k, v in s.items() if k != "outcome"}
            conn.execute(
                "INSERT INTO training_samples (timestamp, source, outcome, features_json) "
                "VALUES (?,?,?,?)",
                (ts, source, outcome, json.dumps(feats, default=str)),
            )
            n += 1
        conn.commit()
    finally:
        conn.close()
    logger.info(f"training_samples: +{n} muestras ({source})")
    return n


def load_samples(db_path: str, source: str | None = None) -> list[dict]:
    """
    Devuelve lista de dicts {**features, 'outcome': ..., 'source': ...}.
    Listo para _extract_features (mismas claves que una fila de signals).
    """
    if not os.path.exists(db_path):
        return []
    ensure_table(db_path)
    conn = sqlite3.connect(db_path)
    try:
        if source:
            rows = conn.execute(
                "SELECT outcome, source, features_json, timestamp FROM training_samples WHERE source=?",
                (source,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT outcome, source, features_json, timestamp FROM training_samples"
            ).fetchall()
    finally:
        conn.close()

    out = []
    for outcome, src, fj, ts in rows:
        try:
            feats = json.loads(fj) if fj else {}
        except Exception:
            feats = {}
        feats["outcome"] = outcome
        feats["source"] = src
        # timestamp de la columna (para orden temporal global en el CV);
        # el del features_json manda si ya existe
        feats.setdefault("timestamp", ts)
        out.append(feats)
    return out


def count_samples(db_path: str) -> dict:
    """Conteo por fuente y outcome (para diagnóstico)."""
    if not os.path.exists(db_path):
        return {}
    ensure_table(db_path)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT source, outcome, COUNT(*) FROM training_samples GROUP BY source, outcome"
        ).fetchall()
    finally:
        conn.close()
    return {f"{s}/{o}": n for s, o, n in rows}

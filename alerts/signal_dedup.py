"""
SignalDedup — Deduplicador de señales PERSISTENTE y centralizado.

Problema que resuelve (bug de spam en Telegram, 2026-07):
  - La deduplicación por stream de signal_engine vive SOLO en memoria
    (`_last_signals`, `_last_dt_signals`, …). Cada reinicio del bot la borra
    y REENVÍA señales idénticas → llegan varias notificaciones iguales.
  - Cada stream (H1 / M15 / SWING / SCALP) tiene su propio dict, sin una
    última línea de defensa común antes de enviar/guardar.

Cómo lo resuelve:
  - Fingerprint estructural por (símbolo | stream | dirección | vela de origen).
    La "vela de origen" se deriva flooreando el timestamp de la señal a la
    granularidad de su timeframe (H1→hora, M15→15 min, …). Así, un reinicio
    dentro de la MISMA vela colapsa al MISMO fingerprint y NO reenvía.
  - Persiste en la tabla `sent_signals` de trades.db → sobrevive reinicios.
  - `should_send()` / `mark_sent()` se llaman en los sitios de emisión de
    main.py, cerrando a la vez el envío a Telegram y el guardado en DB
    (para que los duplicados tampoco contaminen el WR / stats / ML).

Diseño fail-open: ante cualquier error de DB se permite el envío (perder una
señal es peor que un duplicado raro). Todo error se loguea a nivel debug.
"""

from __future__ import annotations

import sqlite3
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Filas más antiguas que esto se podan en cada mark_sent (la ventana de dedup
# es siempre mucho menor, así que no afecta a la lógica; solo mantiene la tabla
# pequeña).
_PRUNE_HOURS = 24


class SignalDedup:
    def __init__(self, db_path: str, config: dict | None = None):
        cfg = (config or {}).get("alerts", {}) if config else {}
        self.db_path     = db_path
        self.enabled     = bool(cfg.get("dedup_enabled", True))
        # Dentro de esta ventana (min), el MISMO fingerprint no se reenvía.
        # Debe cubrir de sobra la duración de una vela H1 para frenar los
        # re-disparos por ciclo y los reenvíos tras reinicio.
        self.window_min  = float(cfg.get("dedup_window_min", 45))
        self._ensure_table()

    # ── infraestructura ────────────────────────────────────────────
    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _ensure_table(self):
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sent_signals (
                    fingerprint TEXT PRIMARY KEY,
                    stream      TEXT,
                    symbol      TEXT,
                    direction   TEXT,
                    sent_at     TEXT NOT NULL
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"[dedup] no se pudo crear sent_signals: {e}")

    # ── fingerprint ────────────────────────────────────────────────
    @staticmethod
    def _stream(sig: dict) -> str:
        """Identifica el stream de forma única: el H1 intraday no lleva `mode`,
        los demás sí (SWING/DAYTRADE/SCALP). El timeframe desempata."""
        return f"{sig.get('mode', 'INTRADAY')}:{sig.get('timeframe', '?')}"

    @classmethod
    def _bar_bucket(cls, sig: dict) -> str:
        """Timestamp de la señal floored a la granularidad de su timeframe.
        Prefiere `bar_time` (vela de origen exacta) si algún día se añade."""
        raw = sig.get("bar_time") or sig.get("timestamp")
        try:
            dt = datetime.fromisoformat(str(raw)) if raw else cls._now()
        except Exception:
            dt = cls._now()

        tf = str(sig.get("timeframe", "H1")).upper()
        try:
            if tf.startswith("M"):                       # M5, M15, M30…
                step = int(tf[1:]) if tf[1:].isdigit() else 15
                dt = dt.replace(minute=(dt.minute // step) * step,
                                second=0, microsecond=0)
            elif tf.startswith("H"):                      # H1, H4…
                step = int(tf[1:]) if tf[1:].isdigit() else 1
                dt = dt.replace(hour=(dt.hour // step) * step,
                                minute=0, second=0, microsecond=0)
            elif tf.startswith("D"):                      # D1
                dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                dt = dt.replace(minute=0, second=0, microsecond=0)
        except Exception:
            pass
        return dt.isoformat()

    @classmethod
    def _fingerprint(cls, sig: dict) -> str:
        return (
            f"{sig.get('symbol', '?')}|{cls._stream(sig)}|"
            f"{sig.get('direction', '?')}|{cls._bar_bucket(sig)}"
        )

    # ── API pública ────────────────────────────────────────────────
    def should_send(self, sig: dict) -> bool:
        """True si esta señal NO se ha enviado ya dentro de la ventana."""
        if not self.enabled or not sig:
            return True
        try:
            fp = self._fingerprint(sig)
            conn = sqlite3.connect(self.db_path, timeout=5)
            row = conn.execute(
                "SELECT sent_at FROM sent_signals WHERE fingerprint = ?", (fp,)
            ).fetchone()
            conn.close()
            if row is None:
                return True
            last = datetime.fromisoformat(row[0])
            age_min = (self._now() - last).total_seconds() / 60.0
            if age_min < self.window_min:
                logger.info(
                    f"[dedup] {sig.get('symbol')} {sig.get('direction')} "
                    f"{self._stream(sig)} ya enviada hace {age_min:.0f} min "
                    f"(ventana {self.window_min:.0f}) — se suprime el duplicado"
                )
                return False
            return True
        except Exception as e:
            logger.debug(f"[dedup] should_send fail-open: {e}")
            return True

    def mark_sent(self, sig: dict):
        """Registra el fingerprint como enviado (y poda filas viejas)."""
        if not self.enabled or not sig:
            return
        try:
            fp     = self._fingerprint(sig)
            now_iso = self._now().isoformat()
            cutoff  = (self._now() - timedelta(hours=_PRUNE_HOURS)).isoformat()
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.execute(
                "INSERT INTO sent_signals (fingerprint, stream, symbol, direction, sent_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(fingerprint) DO UPDATE SET sent_at = excluded.sent_at",
                (fp, self._stream(sig), sig.get("symbol"), sig.get("direction"), now_iso),
            )
            conn.execute("DELETE FROM sent_signals WHERE sent_at < ?", (cutoff,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"[dedup] mark_sent fallo: {e}")

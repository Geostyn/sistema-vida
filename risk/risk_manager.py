"""
Gestión de riesgo — position sizing, límites de drawdown y control de trades.

Mejoras v2:
  - Dynamic risk multiplier: reduce el tamaño de posición tras rachas de pérdidas
    2 LOSS seguidos → 0.5x riesgo | 3+ → 0.25x
  - Weekly drawdown check: además del diario, verifica el límite semanal (4%)
"""

import sqlite3
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, config: dict):
        self.config   = config
        self.risk_cfg = config.get("risk", {})

    # ── Dynamic Risk Multiplier ─────────────────────────────────────

    def get_risk_multiplier(self, db_path: str) -> float:
        """
        Lee los últimos 4 trades cerrados para detectar rachas de pérdidas.
        2 LOSS consecutivos → 0.5x | 3+ LOSS consecutivos → 0.25x | else → 1.0x
        """
        try:
            conn = sqlite3.connect(db_path)
            cur  = conn.cursor()
            cur.execute("""
                SELECT outcome FROM signals
                WHERE outcome IN ('WIN','LOSS')
                ORDER BY timestamp DESC
                LIMIT 4
            """)
            recent = [r[0] for r in cur.fetchall()]
            conn.close()
        except Exception:
            return 1.0

        if not recent:
            return 1.0

        streak = 0
        for outcome in recent:
            if outcome == "LOSS":
                streak += 1
            else:
                break

        if streak >= 3:
            logger.info(f"Risk reducido a 25% — racha de {streak} pérdidas")
            return 0.25
        if streak >= 2:
            logger.info(f"Risk reducido a 50% — racha de {streak} pérdidas")
            return 0.5
        return 1.0

    # ── Position Sizing ─────────────────────────────────────────────

    def calculate_lot_size(self, entry: float, sl: float,
                           symbol: str = "XAUUSD",
                           risk_multiplier: float = 1.0) -> float:
        """
        Calcula el tamaño de posición en lotes.
        risk_multiplier: ajuste por racha (de get_risk_multiplier).
        """
        capital    = float(self.risk_cfg.get("capital", 10000))
        risk_pct   = float(self.risk_cfg.get("risk_per_trade", 0.01))
        risk_usd   = capital * risk_pct * risk_multiplier

        sl_distance = abs(entry - sl)
        if sl_distance == 0:
            logger.warning("SL distance = 0, usando lote mínimo 0.01")
            return 0.01

        if any(x in symbol.upper() for x in ("XAU", "GOLD")):
            risk_per_lot = sl_distance * 100.0
        elif "JPY" in symbol.upper():
            pip_size     = 0.01
            sl_pips      = sl_distance / pip_size
            risk_per_lot = sl_pips * 9.0
        else:
            pip_size     = 0.0001
            sl_pips      = sl_distance / pip_size
            risk_per_lot = sl_pips * 10.0

        if risk_per_lot == 0:
            return 0.01

        lots = risk_usd / risk_per_lot
        lots = max(0.01, round(lots / 0.01) * 0.01)
        return lots

    # ── Daily Drawdown ──────────────────────────────────────────────

    def check_daily_drawdown(self, db_path: str) -> dict:
        max_dd_pct = float(self.risk_cfg.get("max_daily_drawdown", 0.02))
        capital    = float(self.risk_cfg.get("capital", 10000))
        limit_usd  = capital * max_dd_pct

        try:
            conn   = sqlite3.connect(db_path)
            cursor = conn.cursor()
            today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            cursor.execute("""
                SELECT COALESCE(SUM(pnl_amount), 0)
                FROM signals
                WHERE DATE(timestamp) = ?
                  AND outcome IN ('WIN', 'LOSS')
            """, (today,))
            row = cursor.fetchone()
            conn.close()

            daily_pnl  = float(row[0]) if row[0] is not None else 0.0
            daily_loss = abs(min(daily_pnl, 0.0))

            return {
                "ok":           daily_loss < limit_usd,
                "current_loss": daily_loss,
                "limit":        limit_usd,
                "pnl":          daily_pnl,
                "type":         "daily",
            }

        except Exception as e:
            logger.warning(f"Error verificando drawdown diario: {e}")
            return {"ok": True, "current_loss": 0.0, "limit": limit_usd, "pnl": 0.0, "type": "daily"}

    # ── Weekly Drawdown ─────────────────────────────────────────────

    def check_weekly_drawdown(self, db_path: str) -> dict:
        """
        Verifica el drawdown acumulado de los últimos 7 días.
        Límite: max_weekly_drawdown en config (default 4%).
        """
        max_dd_pct = float(self.risk_cfg.get("max_weekly_drawdown", 0.04))
        capital    = float(self.risk_cfg.get("capital", 10000))
        limit_usd  = capital * max_dd_pct

        try:
            conn   = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COALESCE(SUM(pnl_amount), 0)
                FROM signals
                WHERE DATE(timestamp) >= DATE('now', '-7 days')
                  AND outcome IN ('WIN', 'LOSS')
            """)
            row = cursor.fetchone()
            conn.close()

            weekly_pnl  = float(row[0]) if row[0] is not None else 0.0
            weekly_loss = abs(min(weekly_pnl, 0.0))

            return {
                "ok":           weekly_loss < limit_usd,
                "current_loss": weekly_loss,
                "limit":        limit_usd,
                "pnl":          weekly_pnl,
                "type":         "weekly",
            }

        except Exception as e:
            logger.warning(f"Error verificando drawdown semanal: {e}")
            return {"ok": True, "current_loss": 0.0, "limit": limit_usd, "pnl": 0.0, "type": "weekly"}

    # ── Trade Permission ────────────────────────────────────────────

    def can_open_trade(self, active_signals_count: int) -> dict:
        max_sim = int(self.risk_cfg.get("max_simultaneous", 2))
        if active_signals_count >= max_sim:
            return {
                "ok":     False,
                "reason": f"Máx. señales simultáneas alcanzado ({max_sim})",
            }
        return {"ok": True, "reason": ""}

    def is_session_allowed(self) -> dict:
        hours = self.config.get("sessions", {}).get("allowed_hours_utc", {})
        start = int(hours.get("start", 7))
        end   = int(hours.get("end", 20))
        now   = datetime.now(timezone.utc).hour

        if start <= now < end:
            return {"ok": True, "reason": ""}
        return {
            "ok":     False,
            "reason": f"Fuera de horario de trading ({start}:00–{end}:00 UTC)",
        }

    def get_risk_summary(self, db_path: str) -> dict:
        dd      = self.check_daily_drawdown(db_path)
        weekly  = self.check_weekly_drawdown(db_path)
        mult    = self.get_risk_multiplier(db_path)
        return {
            "capital":            float(self.risk_cfg.get("capital", 10000)),
            "risk_per_trade_pct": float(self.risk_cfg.get("risk_per_trade", 0.01)) * 100,
            "risk_multiplier":    mult,
            "effective_risk_pct": float(self.risk_cfg.get("risk_per_trade", 0.01)) * 100 * mult,
            "max_daily_dd_pct":   float(self.risk_cfg.get("max_daily_drawdown", 0.02)) * 100,
            "max_weekly_dd_pct":  float(self.risk_cfg.get("max_weekly_drawdown", 0.04)) * 100,
            "daily_pnl":          dd.get("pnl", 0.0),
            "daily_loss":         dd.get("current_loss", 0.0),
            "daily_limit":        dd.get("limit", 200.0),
            "weekly_pnl":         weekly.get("pnl", 0.0),
            "weekly_loss":        weekly.get("current_loss", 0.0),
            "weekly_limit":       weekly.get("limit", 400.0),
            "within_limits":      dd.get("ok", True) and weekly.get("ok", True),
        }

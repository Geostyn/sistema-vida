"""
Outcome Tracker — Detecta automáticamente WIN/LOSS/EXPIRED para cada señal.

Mejoras v2:
  - EXPIRED: si MAX_BARS_CHECK se alcanza sin tocar SL ni TP → EXPIRED (no LOSS)
  - Auto-Breakeven: cuando el precio llega a +1R, mueve el SL automáticamente
    en MT5 (ya no es solo una alerta Telegram)
"""

import sqlite3
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MAX_BARS_CHECK = 150  # H1 = 6.25 días


class OutcomeTracker:
    def __init__(self, mt5_connector, db_path: str, executor=None):
        self.mt5      = mt5_connector
        self.db_path  = db_path
        self.executor = executor  # TradeExecutor — para auto-BE

    # ── Check outcomes ──────────────────────────────────────────────

    def check_pending_signals(self) -> int:
        pending = self._get_pending_signals()
        if not pending:
            return 0

        updated = 0
        for signal in pending:
            outcome = self._detect_outcome(signal)
            if outcome in ("WIN", "LOSS", "EXPIRED"):
                self._update_outcome(signal["id"], outcome, signal)
                updated += 1
                logger.info(
                    f"Outcome detectado: {signal['symbol']} {signal['direction']} "
                    f"({signal['timestamp'][:16]}) → {outcome}"
                )

        return updated

    def _get_pending_signals(self) -> list:
        try:
            conn   = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, symbol, direction, entry, sl, tp1, tp2, timestamp, mt5_ticket
                FROM signals
                WHERE outcome = 'PENDING'
                  AND sent_telegram = 1
                ORDER BY timestamp ASC
                LIMIT 50
            """)
            rows = cursor.fetchall()
            conn.close()
            cols = ["id", "symbol", "direction", "entry", "sl", "tp1", "tp2", "timestamp", "mt5_ticket"]
            return [dict(zip(cols, r)) for r in rows]
        except Exception as e:
            logger.warning(f"Error leyendo señales pendientes: {e}")
            return []

    def _detect_outcome(self, signal: dict) -> str | None:
        symbol    = signal["symbol"]
        direction = signal["direction"]
        sl        = float(signal["sl"])
        tp1       = float(signal["tp1"])

        df = self.mt5.get_rates(symbol, "H1", 200)
        if df.empty:
            return None

        try:
            sig_time = datetime.fromisoformat(signal["timestamp"])
            if sig_time.tzinfo is None:
                sig_time = sig_time.replace(tzinfo=timezone.utc)
        except Exception:
            return None

        df["time"] = (
            df["time"].dt.tz_localize("UTC")
            if df["time"].dt.tz is None
            else df["time"].dt.tz_convert("UTC")
        )
        future = df[df["time"] > sig_time].head(MAX_BARS_CHECK)

        if future.empty:
            return None

        for _, candle in future.iterrows():
            low  = float(candle["low"])
            high = float(candle["high"])

            if direction == "BUY":
                if low <= sl:
                    return "LOSS"
                if high >= tp1:
                    return "WIN"
            else:
                if high >= sl:
                    return "LOSS"
                if low <= tp1:
                    return "WIN"

        # MAX_BARS_CHECK alcanzado sin resultado → EXPIRED (no es una pérdida real)
        if len(future) >= MAX_BARS_CHECK:
            return "EXPIRED"

        return None

    def _update_outcome(self, signal_id: int, outcome: str, signal: dict = None):
        try:
            conn   = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            pnl_pct    = 0.0
            pnl_amount = 0.0
            if signal and outcome in ("WIN", "LOSS"):
                risk_pct   = 0.01
                rr         = float(signal.get("rr", 2.0))
                capital    = 100000.0
                pnl_pct    = (rr * risk_pct * 100) if outcome == "WIN" else -(risk_pct * 100)
                pnl_amount = (pnl_pct / 100) * capital
            # EXPIRED → P&L = 0 (no es pérdida real)

            cursor.execute(
                "UPDATE signals SET outcome = ?, pnl_pct = ?, pnl_amount = ? WHERE id = ?",
                (outcome, pnl_pct, pnl_amount, signal_id)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error actualizando outcome {signal_id}: {e}")

    # ── Auto-Breakeven ──────────────────────────────────────────────

    def check_breakeven_alerts(self, telegram_bot=None) -> int:
        """
        Cuando el precio llega a +1R:
          - Mueve SL a breakeven automáticamente en MT5 (si hay executor y ticket)
          - Envía confirmación por Telegram
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cur  = conn.cursor()
            cur.execute("""
                SELECT id, symbol, direction, entry, sl, tp1, timestamp, mt5_ticket
                FROM signals
                WHERE outcome = 'PENDING'
                  AND (breakeven_alerted IS NULL OR breakeven_alerted = 0)
                ORDER BY timestamp DESC
                LIMIT 20
            """)
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            logger.warning(f"Breakeven check DB error: {e}")
            return 0

        sent = 0
        for row in rows:
            sig_id, symbol, direction, entry, sl, tp1, ts, mt5_ticket = row
            entry, sl, tp1 = float(entry), float(sl), float(tp1)

            try:
                price_info = self.mt5.get_current_price(symbol)
                current    = float(price_info.get("bid" if direction == "SELL" else "ask", 0))
                if not current:
                    continue
            except Exception:
                continue

            risk   = abs(entry - sl)
            one_r  = entry + risk if direction == "BUY" else entry - risk

            reached_1r = (
                (direction == "BUY"  and current >= one_r) or
                (direction == "SELL" and current <= one_r)
            )

            if not reached_1r:
                continue

            # ── Intentar auto-BE en MT5 ────────────────────────
            auto_ok  = False
            if self.executor and mt5_ticket:
                try:
                    # Verificar que la posición sigue abierta
                    open_tickets = {p.ticket for p in self.executor.get_open_positions()}
                    if int(mt5_ticket) in open_tickets:
                        auto_ok = self.executor.modify_order(
                            int(mt5_ticket), new_sl=entry
                        )
                        if auto_ok:
                            logger.info(
                                f"Auto-BE aplicado: {symbol} {direction} "
                                f"ticket #{mt5_ticket} SL movido a {entry:.5f}"
                            )
                except Exception as ex:
                    logger.warning(f"Auto-BE fallo para ticket #{mt5_ticket}: {ex}")

            # ── Telegram ───────────────────────────────────────
            if telegram_bot:
                if auto_ok:
                    msg = (
                        f"✅ <b>BREAKEVEN AUTOMÁTICO</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 {symbol} {direction} | Ticket #{mt5_ticket}\n"
                        f"💰 Precio: <code>{current:.5f}</code>\n"
                        f"✅ SL movido a <code>{entry:.5f}</code> automáticamente\n"
                        f"🔒 Trade con riesgo CERO — deja correr al TP"
                    )
                elif mt5_ticket:
                    msg = (
                        f"⚠️ <b>AUTO-BE FALLÓ — Acción manual requerida</b>\n"
                        f"📊 {symbol} {direction} | Ticket #{mt5_ticket}\n"
                        f"Mueve el SL a <code>{entry:.5f}</code> en MT5 manualmente\n"
                        f"O usa: <code>/modificar {mt5_ticket} SL:{entry:.5f}</code>"
                    )
                else:
                    msg = (
                        f"🔔 <b>MOVER SL A BREAKEVEN</b>\n"
                        f"📊 {symbol} {direction}\n"
                        f"Precio: <code>{current:.5f}</code> → Entrada: <code>{entry:.5f}</code>"
                    )
                telegram_bot.send_message(msg)
                sent += 1

            # Marcar alerta enviada
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    "UPDATE signals SET breakeven_alerted = 1 WHERE id = ?", (sig_id,)
                )
                conn.commit()
                conn.close()
            except Exception:
                pass

        return sent

    # ── Estadísticas ────────────────────────────────────────────────

    def get_performance_stats(self) -> dict:
        try:
            conn   = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN outcome='WIN'     THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN outcome='LOSS'    THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN outcome='EXPIRED' THEN 1 ELSE 0 END) as expired,
                    AVG(CASE WHEN outcome='WIN' THEN rr ELSE NULL END) as avg_rr_win,
                    AVG(confidence) as avg_confidence
                FROM signals
                WHERE outcome IN ('WIN', 'LOSS', 'EXPIRED')
            """)
            row = cursor.fetchone()
            conn.close()

            if not row or row[0] == 0:
                return {"total": 0, "win_rate": 0, "profit_factor": 0}

            total   = row[0]
            wins    = row[1] or 0
            losses  = row[2] or 0
            expired = row[3] or 0
            avg_rr  = row[4] or 2.0

            closed = wins + losses  # EXPIRED no cuenta para WR
            win_rate      = wins / closed if closed > 0 else 0
            profit_factor = (wins * avg_rr) / losses if losses > 0 else float("inf")

            return {
                "total":         total,
                "wins":          wins,
                "losses":        losses,
                "expired":       expired,
                "win_rate":      round(win_rate, 3),
                "profit_factor": round(profit_factor, 2),
                "avg_rr_win":    round(avg_rr, 2),
            }
        except Exception as e:
            logger.warning(f"Error obteniendo estadísticas: {e}")
            return {"total": 0, "win_rate": 0, "profit_factor": 0}

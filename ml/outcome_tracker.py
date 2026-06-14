"""
Outcome Tracker — Detecta automáticamente WIN/LOSS/EXPIRED para cada señal.

Mejoras v3:
  - EXPIRED: si MAX_BARS_CHECK se alcanza sin tocar SL ni TP → EXPIRED (no LOSS)
  - Auto-Breakeven: cuando el precio llega a +1R, mueve el SL automáticamente
  - P&L REAL desde MT5: si la señal tiene ticket, el outcome y el P&L salen
    del historial de deals de MT5 (incluye parciales, trailing y comisiones)
    en vez del cálculo teórico ±R fijo
  - Fill check: con entradas en retroceso (LIMIT), verifica que la orden se
    EJECUTÓ antes de evaluar SL/TP — una orden nunca ejecutada es EXPIRED,
    no WIN/LOSS fantasma
"""

import sqlite3
import logging
from datetime import datetime, timezone

import MetaTrader5 as mt5

logger = logging.getLogger(__name__)

MAX_BARS_CHECK = 150  # H1 = 6.25 días


class OutcomeTracker:
    def __init__(self, mt5_connector, db_path: str, executor=None, config: dict = None):
        self.mt5      = mt5_connector
        self.db_path  = db_path
        self.executor = executor  # TradeExecutor — para auto-BE
        self.config   = config or {}

    # ── Check outcomes ──────────────────────────────────────────────

    def check_pending_signals(self) -> int:
        pending = self._get_pending_signals()
        if not pending:
            return 0

        updated = 0
        for signal in pending:
            # 1º intento: outcome REAL desde MT5 (si hay ticket)
            real = self._detect_outcome_mt5(signal)
            if real is not None:
                outcome, real_pnl = real
                if outcome == "OPEN":
                    continue  # posición/orden sigue viva en MT5
                self._update_outcome(signal["id"], outcome, signal, real_pnl=real_pnl)
                updated += 1
                logger.info(
                    f"Outcome MT5 real: {signal['symbol']} {signal['direction']} "
                    f"({signal['timestamp'][:16]}) → {outcome} ({real_pnl:+.2f})"
                )
                continue

            # 2º intento: simulación por velas (señales sin ticket)
            outcome = self._detect_outcome(signal)
            if outcome in ("WIN", "LOSS", "EXPIRED"):
                self._update_outcome(signal["id"], outcome, signal)
                updated += 1
                logger.info(
                    f"Outcome detectado: {signal['symbol']} {signal['direction']} "
                    f"({signal['timestamp'][:16]}) → {outcome}"
                )

        return updated

    # ── Outcome real desde MT5 (deals del ticket) ───────────────────

    def _detect_outcome_mt5(self, signal: dict) -> tuple | None:
        """
        Para señales con mt5_ticket, consulta el estado real en MT5.

        Returns:
          ("OPEN", 0)            — orden pendiente o posición aún abierta
          ("WIN"|"LOSS", pnl)    — posición cerrada, P&L real con comisiones
          ("EXPIRED", 0)         — orden LIMIT expiró sin ejecutarse
          None                   — sin ticket o sin info → usar fallback velas
        """
        ticket = signal.get("mt5_ticket")
        if not ticket:
            return None
        ticket = int(ticket)

        try:
            # ¿Sigue como orden pendiente?
            orders = mt5.orders_get(ticket=ticket)
            if orders:
                return ("OPEN", 0.0)

            # ¿Es una posición abierta? (el ticket de la orden = id de posición)
            positions = mt5.positions_get(ticket=ticket)
            if positions:
                return ("OPEN", 0.0)

            # ¿Hay deals cerrados para esta posición?
            deals = mt5.history_deals_get(position=ticket)
            if deals:
                entry_deals = [d for d in deals if d.entry == mt5.DEAL_ENTRY_IN]
                exit_deals  = [d for d in deals if d.entry in
                               (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT,
                                mt5.DEAL_ENTRY_OUT_BY)]
                if not entry_deals:
                    return ("EXPIRED", 0.0)  # nunca se ejecutó
                if not exit_deals:
                    return ("OPEN", 0.0)     # ejecutada pero sin cierre aún

                # Posición ejecutada y cerrada → P&L real total
                pnl = sum(d.profit + d.commission + d.swap + d.fee for d in deals)
                return ("WIN" if pnl > 0 else "LOSS", float(pnl))

            # Sin orden, sin posición, sin deals → la LIMIT expiró sin fill
            hist_orders = mt5.history_orders_get(ticket=ticket)
            if hist_orders:
                return ("EXPIRED", 0.0)

        except Exception as e:
            logger.debug(f"MT5 outcome check #{ticket}: {e}")

        return None

    def _get_pending_signals(self) -> list:
        try:
            conn   = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, symbol, direction, entry, sl, tp1, tp2, timestamp,
                       mt5_ticket, model
                FROM signals
                WHERE outcome = 'PENDING'
                  AND sent_telegram = 1
                ORDER BY timestamp ASC
                LIMIT 50
            """)
            rows = cursor.fetchall()
            conn.close()
            cols = ["id", "symbol", "direction", "entry", "sl", "tp1", "tp2",
                    "timestamp", "mt5_ticket", "model"]
            return [dict(zip(cols, r)) for r in rows]
        except Exception as e:
            logger.warning(f"Error leyendo señales pendientes: {e}")
            return []

    def _detect_outcome(self, signal: dict) -> str | None:
        """
        Simulación por velas para señales SIN ticket MT5.
        Con entradas en retroceso (LIMIT) primero verifica que el precio
        LLEGÓ al entry — si nunca lo tocó, la orden no se ejecutó (EXPIRED).
        """
        symbol    = signal["symbol"]
        direction = signal["direction"]
        entry     = float(signal["entry"])
        sl        = float(signal["sl"])
        tp1       = float(signal["tp1"])

        # Señales DAYTRADE: resolver con velas M15 (SL/TP cortos caben
        # dentro de una sola vela H1) y horizonte intradía propio
        is_dt  = (signal.get("model") == "DAYTRADE")
        dt_cfg = self.config.get("daytrade", {}) or {}
        if is_dt:
            timeframe = "M15"
            n_bars    = 400
            fill_bars = int(dt_cfg.get("expiry_bars_m15", 16))
            max_bars  = int(dt_cfg.get("max_outcome_bars_m15", 96))
        else:
            timeframe = "H1"
            n_bars    = 200
            fill_bars = None
            max_bars  = MAX_BARS_CHECK

        df = self.mt5.get_rates(symbol, timeframe, n_bars)
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
        future = df[df["time"] > sig_time].head(max_bars)

        if future.empty:
            return None

        filled = False
        for bar_idx, (_, candle) in enumerate(future.iterrows()):
            low  = float(candle["low"])
            high = float(candle["high"])

            # ── Fase fill: ¿el precio tocó el entry de la LIMIT? ──
            if not filled:
                # Daytrade: la LIMIT caduca si no llena en la ventana intradía
                if fill_bars is not None and bar_idx >= fill_bars:
                    return "EXPIRED"
                if direction == "BUY" and low <= entry:
                    filled = True
                elif direction == "SELL" and high >= entry:
                    filled = True
                else:
                    continue
                # En la vela del fill, asumir lo conservador: SL primero
                if direction == "BUY" and low <= sl:
                    return "LOSS"
                if direction == "SELL" and high >= sl:
                    return "LOSS"
                continue

            # ── Fase trade: evaluar SL/TP ─────────────────────────
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

        # Horizonte máximo alcanzado sin resultado → EXPIRED
        # (orden nunca ejecutada, o ejecutada sin tocar SL/TP — no es pérdida)
        if len(future) >= max_bars:
            return "EXPIRED"

        return None

    def _update_outcome(self, signal_id: int, outcome: str, signal: dict = None,
                        real_pnl: float = None):
        try:
            conn   = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            pnl_pct    = 0.0
            pnl_amount = 0.0
            capital    = float(
                self.config.get("risk", {}).get("capital", 100000.0)
            )
            if real_pnl is not None and outcome in ("WIN", "LOSS"):
                # P&L real de MT5 (comisiones, parciales y trailing incluidos)
                pnl_amount = float(real_pnl)
                pnl_pct    = (pnl_amount / capital) * 100 if capital > 0 else 0.0
            elif signal and outcome in ("WIN", "LOSS"):
                # Fallback teórico para señales sin ticket
                risk_pct   = float(
                    self.config.get("risk", {}).get("risk_per_trade", 0.01)
                )
                rr         = float(signal.get("rr", 2.0))
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
                SELECT id, symbol, direction, entry, sl, tp1, timestamp, mt5_ticket,
                       funded_apta
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
            sig_id, symbol, direction, entry, sl, tp1, ts, mt5_ticket, funded_apta = row
            entry, sl, tp1 = float(entry), float(sl), float(tp1)

            # Si la orden LIMIT sigue pendiente (sin fill), no hay posición
            # que proteger — saltar para no alertar en falso
            if mt5_ticket:
                try:
                    if mt5.orders_get(ticket=int(mt5_ticket)):
                        continue
                except Exception:
                    pass

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
                funded_line = (
                    "\n🏦 <b>FundedNext:</b> mueve también el SL de tu posición a tu entrada"
                    if funded_apta else ""
                )
                if auto_ok:
                    msg = (
                        f"✅ <b>BREAKEVEN AUTOMÁTICO</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 {symbol} {direction} | Ticket #{mt5_ticket}\n"
                        f"💰 Precio: <code>{current:.5f}</code>\n"
                        f"✅ SL movido a <code>{entry:.5f}</code> automáticamente\n"
                        f"🔒 Trade con riesgo CERO — deja correr al TP{funded_line}"
                    )
                elif mt5_ticket:
                    msg = (
                        f"⚠️ <b>AUTO-BE FALLÓ — Acción manual requerida</b>\n"
                        f"📊 {symbol} {direction} | Ticket #{mt5_ticket}\n"
                        f"Mueve el SL a <code>{entry:.5f}</code> en MT5 manualmente\n"
                        f"O usa: <code>/modificar {mt5_ticket} SL:{entry:.5f}</code>{funded_line}"
                    )
                else:
                    msg = (
                        f"🔔 <b>MOVER SL A BREAKEVEN</b>\n"
                        f"📊 {symbol} {direction}\n"
                        f"Precio: <code>{current:.5f}</code> → Entrada: <code>{entry:.5f}</code>{funded_line}"
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

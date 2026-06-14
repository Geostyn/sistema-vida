"""
TradeManager — Gestión activa de posiciones abiertas para maximizar
el beneficio por operación.

Estrategia (con trading.management.enabled = true):
  - La orden se coloca con TP = TP2 (el runner target), no TP1.
  - Al llegar a +1R el OutcomeTracker ya mueve el SL a breakeven.
  - Al llegar a TP1 (2R): cierre PARCIAL (default 50%) → beneficio asegurado.
  - El resto (runner) sigue con TRAILING STOP de ATR hasta TP2 o trail hit.

Resultado esperado vs TP fijo a 2R:
  - Trade que llega a TP1 y revierte: +1R asegurado (antes +2R, ahora mitad
    al parcial y mitad a BE) — peor caso controlado.
  - Trade que corre en tendencia: +1R parcial + hasta +2R extra del runner
    = hasta +3R por trade (antes capped a +2R).
"""

import sqlite3
import logging
import MetaTrader5 as mt5

logger = logging.getLogger(__name__)


class TradeManager:
    def __init__(self, mt5_connector, executor, db_path: str, config: dict,
                 telegram=None):
        self.mt5      = mt5_connector
        self.executor = executor
        self.db_path  = db_path
        self.config   = config
        self.telegram = telegram
        mgmt = config.get("trading", {}).get("management", {}) or {}
        self.enabled         = bool(mgmt.get("enabled", True))
        self.partial_pct     = float(mgmt.get("partial_close_pct", 0.5))
        self.trail_atr_mult  = float(mgmt.get("trail_atr_mult", 2.0))
        # Último SL notificado por ticket (throttle de avisos de trailing)
        self._last_trail_notified: dict = {}

    # ── Ciclo principal ─────────────────────────────────────────────

    def manage_positions(self) -> int:
        """
        Revisa todas las posiciones abiertas de la estrategia.
        Returns: nº de acciones ejecutadas (parciales + trails).
        """
        if not self.enabled:
            return 0

        actions = 0
        try:
            positions = self.executor.get_open_positions()
        except Exception as e:
            logger.debug(f"TradeManager sin posiciones: {e}")
            return 0

        for pos in positions:
            try:
                actions += self._manage_one(pos)
            except Exception as e:
                logger.warning(f"TradeManager error ticket #{pos.ticket}: {e}")

        return actions

    # ── Gestión de una posición ─────────────────────────────────────

    def _manage_one(self, pos) -> int:
        sig = self._get_signal_by_ticket(pos.ticket)
        if not sig:
            return 0

        direction = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
        entry     = float(pos.price_open)
        tp1       = float(sig["tp1"]) if sig["tp1"] else None
        if tp1 is None:
            return 0

        tick = self.mt5.get_current_price(pos.symbol)
        if not tick:
            return 0
        # Precio al que podríamos CERRAR la posición ahora
        close_price = float(tick["bid"]) if direction == "BUY" else float(tick["ask"])

        partial_done = bool(sig.get("partial_closed", 0))

        # ── Fase 1: cierre parcial al tocar TP1 ──────────────────
        if not partial_done:
            tp1_reached = (
                (direction == "BUY"  and close_price >= tp1) or
                (direction == "SELL" and close_price <= tp1)
            )
            if tp1_reached:
                return self._do_partial_close(pos, sig, direction, entry, close_price)
            return 0

        # ── Fase 2: trailing stop del runner ─────────────────────
        return self._do_trailing(pos, direction, entry, close_price, sig)

    def _do_partial_close(self, pos, sig, direction, entry, price) -> int:
        sym_info = mt5.symbol_info(pos.symbol)
        if sym_info is None:
            return 0
        vol_step = sym_info.volume_step
        vol_min  = sym_info.volume_min

        vol_close = round(pos.volume * self.partial_pct / vol_step) * vol_step
        vol_close = round(vol_close, 2)

        # Si la posición es demasiado pequeña para partir → solo asegurar BE
        if vol_close < vol_min or (pos.volume - vol_close) < vol_min:
            self._ensure_breakeven(pos, direction, entry)
            self._mark_partial(sig["id"])
            return 1

        ok = self.executor.close_position(pos.ticket, volume=vol_close)
        if not ok:
            return 0

        # SL a breakeven para el runner (si el tracker no lo hizo ya)
        self._ensure_breakeven(pos, direction, entry)
        self._mark_partial(sig["id"])

        if self.telegram:
            funded_line = ""
            if sig.get("funded_apta"):
                f_lots = float(sig.get("funded_lots") or 0.01)
                funded_line = (
                    f"\n🏦 <b>FundedNext:</b> cierra ~50% (≈{max(0.01, f_lots/2):.2f} lotes) "
                    f"y mueve el SL a tu entrada (BE)"
                )
            self.telegram.send_message(
                f"💰 <b>BENEFICIO PARCIAL ASEGURADO</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 {pos.symbol} {direction} | Ticket #{pos.ticket}\n"
                f"✅ Cerrado {vol_close:.2f} lotes ({self.partial_pct:.0%}) en TP1\n"
                f"🏃 Runner: {pos.volume - vol_close:.2f} lotes → TP2 con trailing\n"
                f"🔒 SL del runner en breakeven — riesgo CERO{funded_line}"
            )
        logger.info(
            f"[TradeManager] Parcial #{pos.ticket}: {vol_close:.2f} lotes "
            f"cerrados en TP1 @ {price:.5f}, runner con trailing"
        )
        return 1

    def _do_trailing(self, pos, direction, entry, price, sig: dict = None) -> int:
        atr = self._current_atr(pos.symbol)
        if atr <= 0:
            return 0

        current_sl = float(pos.sl) if pos.sl else 0.0
        moved_sl   = None

        if direction == "BUY":
            new_sl = price - atr * self.trail_atr_mult
            new_sl = max(new_sl, entry)  # nunca por debajo de breakeven
            # Solo mover si mejora al menos 0.1 ATR (evitar spam de modifies)
            if new_sl > current_sl + atr * 0.1:
                if self.executor.modify_order(pos.ticket, new_sl=round(new_sl, 5)):
                    moved_sl = new_sl
        else:
            new_sl = price + atr * self.trail_atr_mult
            new_sl = min(new_sl, entry)
            if current_sl == 0.0 or new_sl < current_sl - atr * 0.1:
                if self.executor.modify_order(pos.ticket, new_sl=round(new_sl, 5)):
                    moved_sl = new_sl

        if moved_sl is None:
            return 0

        logger.info(
            f"[TradeManager] Trail #{pos.ticket} SL → {moved_sl:.5f} "
            f"(precio {price:.5f})"
        )

        # Aviso Telegram con throttle: solo si el SL avanzó >= 0.5 ATR desde
        # el último aviso (para que el usuario replique en la cuenta 2K sin spam)
        if self.telegram:
            last_notified = self._last_trail_notified.get(pos.ticket)
            if last_notified is None or abs(moved_sl - last_notified) >= atr * 0.5:
                funded_line = ""
                if sig and sig.get("funded_apta"):
                    funded_line = "\n🏦 <b>FundedNext:</b> replica este SL en tu posición"
                self.telegram.send_message(
                    f"🏃 <b>TRAILING ACTUALIZADO</b>\n"
                    f"📊 {pos.symbol} {direction} | Ticket #{pos.ticket}\n"
                    f"🛑 Nuevo SL: <code>{moved_sl:.5f}</code> "
                    f"(precio {price:.5f}){funded_line}"
                )
                self._last_trail_notified[pos.ticket] = moved_sl
        return 1

    # ── Helpers ─────────────────────────────────────────────────────

    def _ensure_breakeven(self, pos, direction, entry):
        current_sl = float(pos.sl) if pos.sl else 0.0
        needs_be = (
            (direction == "BUY"  and current_sl < entry) or
            (direction == "SELL" and (current_sl == 0.0 or current_sl > entry))
        )
        if needs_be:
            try:
                self.executor.modify_order(pos.ticket, new_sl=entry)
            except Exception as e:
                logger.debug(f"BE en parcial falló #{pos.ticket}: {e}")

    def _current_atr(self, symbol: str) -> float:
        try:
            from analysis.indicators import add_indicators
            df = self.mt5.get_rates(symbol, "H1", 60)
            if df.empty:
                return 0.0
            df = add_indicators(df)
            return float(df["atr"].iloc[-1])
        except Exception:
            return 0.0

    def _get_signal_by_ticket(self, ticket: int) -> dict | None:
        try:
            conn = sqlite3.connect(self.db_path)
            cur  = conn.cursor()
            cur.execute("""
                SELECT id, direction, entry, sl, tp1, tp2, partial_closed,
                       funded_apta, funded_lots
                FROM signals WHERE mt5_ticket = ?
                ORDER BY id DESC LIMIT 1
            """, (int(ticket),))
            row = cur.fetchone()
            conn.close()
            if not row:
                return None
            cols = ["id", "direction", "entry", "sl", "tp1", "tp2", "partial_closed",
                    "funded_apta", "funded_lots"]
            return dict(zip(cols, row))
        except Exception as e:
            logger.debug(f"TradeManager DB error: {e}")
            return None

    def _mark_partial(self, signal_id: int):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "UPDATE signals SET partial_closed = 1 WHERE id = ?", (signal_id,)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"mark_partial error: {e}")

"""
TradeExecutor — Ejecución automática de órdenes en MetaTrader 5.

Coloca órdenes LIMIT pendientes cuando el sistema detecta un setup válido.
El usuario recibe una notificación en Telegram y puede cancelar con /cancelar <ticket>.
"""

import MetaTrader5 as mt5
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

MAGIC_NUMBER = 20260601  # identificador de esta estrategia en MT5


class TradeExecutor:
    def __init__(self, mt5_connector, config: dict):
        self.mt5    = mt5_connector
        self.config = config
        self._trade_cfg = config.get("trading", {})

    # ──────────────────────────────────────────────────────────────
    # Colocar orden LIMIT (pendiente — espera que el precio llegue)
    # ──────────────────────────────────────────────────────────────

    def place_limit_order(self, symbol: str, direction: str, volume: float,
                          entry: float, sl: float, tp: float,
                          comment: str = "SMC-H1") -> Optional[int]:
        """
        Coloca una orden LIMIT pendiente en MT5.
        La orden se ejecuta automáticamente cuando el precio alcance `entry`.

        Returns:
            ticket (int) si la orden fue aceptada, None si falló.
        """
        if not self.mt5.connected:
            logger.error("MT5 no conectado — no se puede colocar orden")
            return None

        sym_info = mt5.symbol_info(symbol)
        if sym_info is None:
            logger.error(f"Símbolo {symbol} no disponible en MT5")
            return None

        # Asegurar que el símbolo está visible en MarketWatch
        if not sym_info.visible:
            mt5.symbol_select(symbol, True)

        # Validar y ajustar volumen al step del broker
        vol_step = sym_info.volume_step
        vol_min  = sym_info.volume_min
        vol_max  = sym_info.volume_max
        volume   = max(vol_min, min(vol_max, round(volume / vol_step) * vol_step))

        # Validar distancia mínima de stops (trade_stops_level en puntos)
        stops_pts = sym_info.trade_stops_level * sym_info.point
        tick      = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f"Sin tick para {symbol}")
            return None

        ref_price = tick.ask if direction == "BUY" else tick.bid

        # Ajustar SL/TP si están demasiado cerca
        if direction == "BUY":
            sl = min(sl, entry - stops_pts)
            tp = max(tp, entry + stops_pts)
        else:
            sl = max(sl, entry + stops_pts)
            tp = min(tp, entry - stops_pts)

        order_type = mt5.ORDER_TYPE_BUY_LIMIT if direction == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT

        magic    = int(self._trade_cfg.get("magic_number", MAGIC_NUMBER))
        expiry_h = int(self._trade_cfg.get("order_expiry_hours", 8))
        expiry   = datetime.now(timezone.utc) + timedelta(hours=expiry_h)

        request = {
            "action":      mt5.TRADE_ACTION_PENDING,
            "symbol":      symbol,
            "volume":      round(volume, 2),
            "type":        order_type,
            "price":       round(entry, sym_info.digits),
            "sl":          round(sl,    sym_info.digits),
            "tp":          round(tp,    sym_info.digits),
            "deviation":   10,
            "magic":       magic,
            "comment":     comment[:27],
            "type_time":   mt5.ORDER_TIME_SPECIFIED,
            "expiration":  int(expiry.timestamp()),
        }

        result = mt5.order_send(request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code    = result.retcode if result else "None"
            comment = result.comment if result else str(mt5.last_error())
            logger.error(f"Orden falló [{code}]: {comment}")
            return None

        ticket = result.order
        logger.info(
            f"[TICKET {ticket}] LIMIT {direction} {symbol} "
            f"@ {entry:.5f} | SL:{sl:.5f} TP:{tp:.5f} | Vol:{volume:.2f}"
        )
        return ticket

    # ──────────────────────────────────────────────────────────────
    # Cancelar orden pendiente
    # ──────────────────────────────────────────────────────────────

    def cancel_pending_order(self, ticket: int) -> bool:
        """Cancela una orden LIMIT/STOP pendiente por ticket."""
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order":  ticket,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = result.retcode if result else "None"
            logger.error(f"Cancelación {ticket} falló [{code}]")
            return False
        logger.info(f"[TICKET {ticket}] Orden cancelada")
        return True

    # ──────────────────────────────────────────────────────────────
    # Modificar SL/TP de orden pendiente o posición abierta
    # ──────────────────────────────────────────────────────────────

    def modify_order(self, ticket: int,
                     new_sl: Optional[float] = None,
                     new_tp: Optional[float] = None) -> bool:
        """Modifica SL y/o TP de una orden pendiente o posición abierta."""
        # Intentar como orden pendiente primero
        order = mt5.order_get(ticket=ticket)
        if order:
            request = {
                "action": mt5.TRADE_ACTION_MODIFY,
                "order":  ticket,
                "price":  order.price_open,
                "sl":     new_sl if new_sl is not None else order.sl,
                "tp":     new_tp if new_tp is not None else order.tp,
            }
        else:
            # Intentar como posición abierta
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                logger.error(f"Ticket {ticket} no encontrado")
                return False
            pos = positions[0]
            request = {
                "action":   mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "sl":       new_sl if new_sl is not None else pos.sl,
                "tp":       new_tp if new_tp is not None else pos.tp,
            }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = result.retcode if result else "None"
            logger.error(f"Modificación {ticket} falló [{code}]")
            return False
        logger.info(f"[TICKET {ticket}] Modificado — SL:{new_sl} TP:{new_tp}")
        return True

    # ──────────────────────────────────────────────────────────────
    # Cerrar posición abierta
    # ──────────────────────────────────────────────────────────────

    def close_position(self, ticket: int, volume: Optional[float] = None) -> bool:
        """Cierra una posición abierta a precio de mercado."""
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            logger.error(f"Posición {ticket} no encontrada")
            return False

        pos  = positions[0]
        vol  = volume if volume else pos.volume
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return False

        # Dirección opuesta para cerrar
        if pos.type == mt5.POSITION_TYPE_BUY:
            order_type   = mt5.ORDER_TYPE_SELL
            close_price  = tick.bid
        else:
            order_type   = mt5.ORDER_TYPE_BUY
            close_price  = tick.ask

        request = {
            "action":   mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol":   pos.symbol,
            "volume":   vol,
            "type":     order_type,
            "price":    close_price,
            "deviation": 10,
            "magic":    MAGIC_NUMBER,
            "comment":  f"Close {ticket}",
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = result.retcode if result else "None"
            logger.error(f"Cierre {ticket} falló [{code}]")
            return False
        logger.info(f"[TICKET {ticket}] Posición cerrada @ {close_price}")
        return True

    # ──────────────────────────────────────────────────────────────
    # Consultas
    # ──────────────────────────────────────────────────────────────

    def get_pending_orders(self) -> list:
        """Lista de órdenes LIMIT/STOP pendientes de esta estrategia."""
        orders = mt5.orders_get()
        if orders is None:
            return []
        magic = int(self._trade_cfg.get("magic_number", MAGIC_NUMBER))
        return [o for o in orders if o.magic == magic]

    def get_open_positions(self) -> list:
        """Lista de posiciones abiertas de esta estrategia."""
        positions = mt5.positions_get()
        if positions is None:
            return []
        magic = int(self._trade_cfg.get("magic_number", MAGIC_NUMBER))
        return [p for p in positions if p.magic == magic]

    # ──────────────────────────────────────────────────────────────
    # Limpieza de órdenes expiradas
    # ──────────────────────────────────────────────────────────────

    def cleanup_expired_orders(self) -> list:
        """
        Cancela órdenes pendientes que exceden el tiempo de expiración configurado.
        MT5 debería cancelarlas solo (ORDER_TIME_SPECIFIED), pero esto sirve de
        respaldo y devuelve la lista de tickets cancelados para notificar.
        """
        max_hours = int(self._trade_cfg.get("order_expiry_hours", 8))
        cutoff    = datetime.now(timezone.utc) - timedelta(hours=max_hours)
        cancelled = []

        for order in self.get_pending_orders():
            order_time = datetime.fromtimestamp(order.time_setup, tz=timezone.utc)
            if order_time < cutoff:
                if self.cancel_pending_order(order.ticket):
                    cancelled.append({
                        "ticket":    order.ticket,
                        "symbol":    order.symbol,
                        "direction": "BUY" if order.type == mt5.ORDER_TYPE_BUY_LIMIT else "SELL",
                        "entry":     order.price_open,
                    })
                    logger.info(f"[TICKET {order.ticket}] Expirada y cancelada (>{max_hours}h)")

        return cancelled

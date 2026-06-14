"""
MT5 Connector — Conexión con MetaTrader 5 y descarga de datos OHLCV.
"""

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Mapa de nombres legibles a constantes de MT5
TIMEFRAMES = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}


class MT5Connector:
    def __init__(self, config: dict):
        self.config = config
        self.connected = False

    def connect(self) -> bool:
        """Inicializa y conecta a MetaTrader 5."""
        if not mt5.initialize():
            logger.error(f"mt5.initialize() falló: {mt5.last_error()}")
            return False

        mt5_cfg = self.config.get("mt5", {})
        account  = mt5_cfg.get("account", 0)
        password = mt5_cfg.get("password", "")
        server   = mt5_cfg.get("server", "")

        # Solo hace login si se configuraron las credenciales explícitamente
        if account and password and server:
            if not mt5.login(account, password=password, server=server):
                logger.error(f"mt5.login() falló: {mt5.last_error()}")
                mt5.shutdown()
                return False

        info = mt5.account_info()
        if info is None:
            logger.error("Sin info de cuenta. ¿MetaTrader 5 está abierto?")
            return False

        self.connected = True
        logger.info(f"MT5 conectado | Cuenta: {info.login} | Servidor: {info.server} | Balance: {info.balance}")
        return True

    def disconnect(self):
        """Cierra la conexión con MT5."""
        mt5.shutdown()
        self.connected = False
        logger.info("MT5 desconectado.")

    def _ensure_connected(self):
        if not self.connected:
            self.connect()

    def test_connection(self) -> dict:
        """Prueba la conexión y devuelve info de la cuenta."""
        self._ensure_connected()
        info = mt5.account_info()
        if info is None:
            return {"ok": False, "error": str(mt5.last_error())}
        return {
            "ok": True,
            "login": info.login,
            "server": info.server,
            "balance": info.balance,
            "equity": info.equity,
            "currency": info.currency,
            "leverage": info.leverage,
        }

    def get_rates(self, symbol: str, timeframe: str, n_bars: int = 500) -> pd.DataFrame:
        """
        Descarga las últimas n_bars velas OHLCV del símbolo y timeframe.
        Devuelve DataFrame con: time, open, high, low, close, volume
        """
        self._ensure_connected()

        tf = TIMEFRAMES.get(timeframe)
        if tf is None:
            raise ValueError(f"Timeframe '{timeframe}' no válido. Opciones: {list(TIMEFRAMES.keys())}")

        rates = mt5.copy_rates_from_pos(symbol, tf, 0, n_bars)
        if rates is None or len(rates) == 0:
            logger.warning(f"Sin datos para {symbol} {timeframe}: {mt5.last_error()}")
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df[["time", "open", "high", "low", "close", "tick_volume"]].copy()
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def get_rates_range(self, symbol: str, timeframe: str,
                        date_from: datetime, date_to: datetime) -> pd.DataFrame:
        """Descarga datos entre dos fechas. Usado para backtesting."""
        self._ensure_connected()

        tf = TIMEFRAMES.get(timeframe)
        if tf is None:
            raise ValueError(f"Timeframe '{timeframe}' no válido.")

        rates = mt5.copy_rates_range(symbol, tf, date_from, date_to)
        if rates is None or len(rates) == 0:
            logger.warning(f"Sin datos históricos para {symbol} {timeframe}")
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df[["time", "open", "high", "low", "close", "tick_volume"]].copy()
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def get_current_price(self, symbol: str) -> dict:
        """Devuelve precio actual bid/ask y spread."""
        self._ensure_connected()
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return {}
        return {
            "symbol": symbol,
            "bid":    tick.bid,
            "ask":    tick.ask,
            "spread": round(tick.ask - tick.bid, 5),
            "time":   datetime.fromtimestamp(tick.time),
        }

    def get_symbol_info(self, symbol: str) -> dict:
        """Información del símbolo: decimales, tamaño de contrato, lotes min/max."""
        self._ensure_connected()
        info = mt5.symbol_info(symbol)
        if info is None:
            return {}
        return {
            "symbol":              symbol,
            "digits":              info.digits,
            "point":               info.point,
            "trade_contract_size": info.trade_contract_size,
            "volume_min":          info.volume_min,
            "volume_max":          info.volume_max,
            "volume_step":         info.volume_step,
        }

    def get_account_info(self) -> dict:
        """Balance, equity y margen de la cuenta."""
        self._ensure_connected()
        info = mt5.account_info()
        if info is None:
            return {}
        return {
            "balance":     info.balance,
            "equity":      info.equity,
            "margin":      info.margin,
            "free_margin": info.margin_free,
            "profit":      info.profit,
            "currency":    info.currency,
        }

    def copy_ticks_range(self, symbol: str, date_from: datetime,
                          date_to: datetime, flags=None):
        """
        Descarga tick data entre dos fechas.
        flags: mt5.COPY_TICKS_ALL | COPY_TICKS_TRADE | COPY_TICKS_INFO
        Retorna numpy array o None.
        """
        self._ensure_connected()
        if flags is None:
            flags = mt5.COPY_TICKS_TRADE
        ticks = mt5.copy_ticks_range(symbol, date_from, date_to, flags)
        if ticks is None or len(ticks) == 0:
            logger.debug(f"Sin ticks para {symbol}: {mt5.last_error()}")
            return None
        return ticks

    def get_positions(self, magic: int = None) -> list:
        """Posiciones abiertas, opcionalmente filtradas por magic number."""
        self._ensure_connected()
        positions = mt5.positions_get()
        if positions is None:
            return []
        if magic is not None:
            return [p for p in positions if p.magic == magic]
        return list(positions)

    def get_pending_orders(self, magic: int = None) -> list:
        """Órdenes pendientes (LIMIT/STOP), opcionalmente filtradas por magic."""
        self._ensure_connected()
        orders = mt5.orders_get()
        if orders is None:
            return []
        if magic is not None:
            return [o for o in orders if o.magic == magic]
        return list(orders)

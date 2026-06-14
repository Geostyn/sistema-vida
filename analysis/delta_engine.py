"""
Delta Engine — Análisis de flujo de órdenes usando tick data de MT5.

Delta = volumen_compradores - volumen_vendedores en cada vela.
Tick flags de MT5:
  TICK_FLAG_BUY  = 128  →  transacción iniciada por comprador (lifted offer)
  TICK_FLAG_SELL = 256  →  transacción iniciada por vendedor (hit bid)

Confluencias que añade:
  +1.0  Delta cumulative positivo (BUY) o negativo (SELL) en últimas 3 barras
  -0.5  Delta divergence: precio en HH pero delta negativo (debilidad oculta)
  +0.5  Absorción detectada: precio plano + delta alto (acumulación institucional)
"""

import MetaTrader5 as mt5
import numpy as np
import pandas as pd
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

TICK_FLAG_BUY  = 128
TICK_FLAG_SELL = 256


class DeltaEngine:
    def __init__(self, mt5_connector):
        self.mt5    = mt5_connector
        self._cache = {}  # symbol -> {bar_time -> delta_value}

    # ──────────────────────────────────────────────────────────────
    # Delta de una sola barra H1
    # ──────────────────────────────────────────────────────────────

    def calculate_bar_delta(self, symbol: str, bar_time: datetime,
                             bar_minutes: int = 60) -> float:
        """
        Calcula el delta normalizado de una barra H1.
        Delta = (buy_vol - sell_vol) / total_vol  →  rango [-1, +1]

        +1.0 = toda la actividad fue compradora
        -1.0 = toda la actividad fue vendedora
         0.0 = equilibrio o sin datos
        """
        cache_key = f"{symbol}_{bar_time.isoformat()}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        if not self.mt5.connected:
            return 0.0

        dt_from = bar_time
        dt_to   = bar_time + timedelta(minutes=bar_minutes)

        try:
            ticks = mt5.copy_ticks_range(
                symbol, dt_from, dt_to, mt5.COPY_TICKS_TRADE
            )
        except Exception as e:
            logger.debug(f"Tick data {symbol} error: {e}")
            return 0.0

        if ticks is None or len(ticks) == 0:
            return 0.0

        ticks_df = pd.DataFrame(ticks)
        if "flags" not in ticks_df.columns or "volume" not in ticks_df.columns:
            return 0.0

        buy_mask  = ticks_df["flags"].astype(int) & TICK_FLAG_BUY  > 0
        sell_mask = ticks_df["flags"].astype(int) & TICK_FLAG_SELL > 0

        buy_vol  = float(ticks_df.loc[buy_mask,  "volume"].sum())
        sell_vol = float(ticks_df.loc[sell_mask, "volume"].sum())
        total    = buy_vol + sell_vol

        delta = (buy_vol - sell_vol) / total if total > 0 else 0.0
        delta = round(delta, 4)

        self._cache[cache_key] = delta

        # Limpiar cache antigua (mantener solo últimas 100 entradas)
        if len(self._cache) > 100:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]

        return delta

    # ──────────────────────────────────────────────────────────────
    # Delta acumulado de múltiples barras
    # ──────────────────────────────────────────────────────────────

    def get_cumulative_delta(self, symbol: str, df_h1: pd.DataFrame,
                              n_bars: int = 3) -> dict:
        """
        Calcula el delta acumulado y detecta patrones para las últimas n_bars barras.

        Returns dict con:
          cum_delta      : promedio de deltas en n_bars
          divergence     : True si precio sube pero delta baja (o viceversa)
          absorption     : True si precio plano + delta alto (acumulación)
          bars_computed  : número de barras con datos reales
        """
        if df_h1.empty or len(df_h1) < n_bars + 1:
            return {"cum_delta": 0.0, "divergence": False, "absorption": False, "bars_computed": 0}

        recent_bars = df_h1.tail(n_bars)
        deltas      = []

        for _, row in recent_bars.iterrows():
            bar_time = row["time"]
            if hasattr(bar_time, "to_pydatetime"):
                bar_time = bar_time.to_pydatetime()
            if bar_time.tzinfo is None:
                bar_time = bar_time.replace(tzinfo=timezone.utc)
            d = self.calculate_bar_delta(symbol, bar_time)
            deltas.append(d)

        if not deltas:
            return {"cum_delta": 0.0, "divergence": False, "absorption": False, "bars_computed": 0}

        cum_delta = float(np.mean(deltas))

        # Divergencia: precio hace HH pero delta negativo (BUY debilidad oculta)
        price_direction = 1 if recent_bars["close"].iloc[-1] > recent_bars["close"].iloc[0] else -1
        divergence = (price_direction > 0 and cum_delta < -0.15) or \
                     (price_direction < 0 and cum_delta > +0.15)

        # Absorción: rango estrecho (precio plano) con delta absoluto alto
        price_range = float((recent_bars["high"].max() - recent_bars["low"].min()) /
                             recent_bars["close"].mean()) if recent_bars["close"].mean() else 0
        absorption = price_range < 0.003 and abs(cum_delta) > 0.25

        bars_with_data = sum(1 for d in deltas if d != 0.0)

        return {
            "cum_delta":   round(cum_delta, 3),
            "divergence":  divergence,
            "absorption":  absorption,
            "bars_computed": bars_with_data,
            "deltas":      deltas,
        }

    # ──────────────────────────────────────────────────────────────
    # Confluencia para el motor de señales
    # ──────────────────────────────────────────────────────────────

    def get_confluence(self, symbol: str, direction: str,
                        df_h1: pd.DataFrame, n_bars: int = 3) -> tuple[float, str]:
        """
        Calcula la confluencia de delta para el setup.

        Returns:
            (score, description) donde score puede ser positivo o negativo.
        """
        result = self.get_cumulative_delta(symbol, df_h1, n_bars=n_bars)

        if result["bars_computed"] == 0:
            return 0.0, ""

        cum     = result["cum_delta"]
        diverg  = result["divergence"]
        absorpt = result["absorption"]

        # Penalización por divergencia (señal contraria)
        if diverg:
            return -0.5, f"Delta divergence ({cum:+.2f}) — debilidad oculta"

        # Absorción institucional alineada con dirección
        if absorpt and ((direction == "BUY" and cum > 0) or (direction == "SELL" and cum < 0)):
            return 1.0, f"Delta absorción institucional ({cum:+.2f})"

        # Imbalance normal alineado
        threshold = 0.20
        if direction == "BUY"  and cum >  threshold:
            return 1.0, f"Delta BUY dominante ({cum:+.2f})"
        if direction == "SELL" and cum < -threshold:
            return 1.0, f"Delta SELL dominante ({cum:+.2f})"

        # Imbalance débil (>=0.1)
        weak_threshold = 0.10
        if direction == "BUY"  and cum >  weak_threshold:
            return 0.5, f"Delta BUY leve ({cum:+.2f})"
        if direction == "SELL" and cum < -weak_threshold:
            return 0.5, f"Delta SELL leve ({cum:+.2f})"

        return 0.0, ""

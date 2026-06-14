"""
Indicadores técnicos — EMA, ATR, RSI implementados con pandas puro.
Sin dependencias externas problemáticas.
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Añade EMA 20/50/200, ATR 14 y RSI 14 al DataFrame.
    Requiere columnas: open, high, low, close, volume
    """
    if df.empty:
        return df

    if len(df) < 50:
        logger.warning(f"Solo {len(df)} velas — indicadores poco confiables")
        return df

    df = df.copy()

    # ── EMA (Exponential Moving Average) ──────────────────────
    # Fórmula: precio * α + EMA_anterior * (1-α)   donde α = 2/(período+1)
    df["ema_20"]  = df["close"].ewm(span=20,  adjust=False).mean()
    df["ema_50"]  = df["close"].ewm(span=50,  adjust=False).mean()
    df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()

    # ── ATR (Average True Range) — mide la volatilidad ────────
    # True Range = max(high-low, |high-close_anterior|, |low-close_anterior|)
    prev_close   = df["close"].shift(1)
    high_low     = df["high"] - df["low"]
    high_pc      = (df["high"] - prev_close).abs()
    low_pc       = (df["low"]  - prev_close).abs()
    true_range   = pd.concat([high_low, high_pc, low_pc], axis=1).max(axis=1)
    df["atr"]    = true_range.ewm(span=14, adjust=False).mean()

    # ── RSI (Relative Strength Index) — momentum ──────────────
    # Mide la fuerza relativa de subidas vs bajadas en los últimos 14 períodos
    delta     = df["close"].diff()
    gain      = delta.where(delta > 0, 0.0)
    loss      = (-delta).where(delta < 0, 0.0)
    avg_gain  = gain.ewm(com=13, adjust=False).mean()   # com=período-1
    avg_loss  = loss.ewm(com=13, adjust=False).mean()
    rs        = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # Eliminar primeras filas con NaN
    df.dropna(subset=["atr", "rsi"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def get_ema_bias(df: pd.DataFrame) -> str:
    """
    Sesgo de mercado basado en EMAs.
    - BULLISH: precio > EMA200 y EMA20 > EMA50
    - BEARISH: precio < EMA200 y EMA20 < EMA50
    - NEUTRAL: señales contradictorias
    """
    if df.empty:
        return "NEUTRAL"

    last   = df.iloc[-1]
    price  = last.get("close")
    ema20  = last.get("ema_20")
    ema50  = last.get("ema_50")
    ema200 = last.get("ema_200")

    if any(v is None or pd.isna(v) for v in [price, ema20, ema50]):
        return "NEUTRAL"

    if ema200 is not None and not pd.isna(ema200):
        if price > ema200 and ema20 > ema50:
            return "BULLISH"
        if price < ema200 and ema20 < ema50:
            return "BEARISH"
    else:
        if ema20 > ema50:
            return "BULLISH"
        if ema20 < ema50:
            return "BEARISH"

    return "NEUTRAL"


def get_rsi_state(df: pd.DataFrame) -> str:
    """
    Clasifica el RSI:
    - < 35  → OVERSOLD  (zona de posible compra)
    - > 65  → OVERBOUGHT (zona de posible venta)
    - resto → NEUTRAL
    """
    if df.empty:
        return "NEUTRAL"

    rsi = df.iloc[-1].get("rsi")
    if rsi is None or pd.isna(rsi):
        return "NEUTRAL"

    if rsi < 35:
        return "OVERSOLD"
    if rsi > 65:
        return "OVERBOUGHT"
    return "NEUTRAL"

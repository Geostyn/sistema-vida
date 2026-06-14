import pandas as pd
import numpy as np
import sys
import random

sys.path.insert(0, ".")
from analysis.indicators import add_indicators, get_ema_bias, get_rsi_state

random.seed(42)
prices = [2300.0]
for _ in range(299):
    prices.append(prices[-1] + random.uniform(-5, 5))

df = pd.DataFrame({
    "time":   pd.date_range("2025-01-01", periods=300, freq="1h"),
    "open":   prices,
    "high":   [p + random.uniform(0, 3) for p in prices],
    "low":    [p - random.uniform(0, 3) for p in prices],
    "close":  [p + random.uniform(-2, 2) for p in prices],
    "volume": [random.randint(100, 1000) for _ in prices],
})

df = add_indicators(df)
bias = get_ema_bias(df)
rsi  = get_rsi_state(df)

print("=" * 40)
print("  INDICADORES: OK")
print("=" * 40)
print("  Filas totales: " + str(len(df)))
print("  Columnas:      " + str(list(df.columns)))
print("  EMA20 actual:  " + str(round(float(df["ema_20"].iloc[-1]), 2)))
print("  EMA50 actual:  " + str(round(float(df["ema_50"].iloc[-1]), 2)))
print("  ATR actual:    " + str(round(float(df["atr"].iloc[-1]), 2)))
print("  RSI actual:    " + str(round(float(df["rsi"].iloc[-1]), 1)))
print("  Bias actual:   " + bias)
print("  RSI estado:    " + rsi)
print("=" * 40)
print("  Sin pandas_ta, funciona con Python 3.14")
print("=" * 40)

"""
VWAP de sesión + bandas de desviación (herramienta de la investigación 2026-06-19).

Idea (institucional): los algos benchmarkean ejecución contra VWAP (compran debajo,
venden encima) → pull gravitacional hacia VWAP. Bandas a ±kσ marcan extremos donde
la reversión a la media es probable... PERO solo en RANGO: si VWAP tiene pendiente
fuerte (tendencia) la reversión falla. Por eso `vwap_slope` sirve de filtro de régimen.

XAUUSD spot solo tiene tick-volume (no centralizado) → VWAP es un proxy razonable.
Sin estado, sin dependencias nuevas. Reset diario 00:00 UTC (sesión global del bot).
"""

import numpy as np
import pandas as pd


def session_vwap_bands(df: "pd.DataFrame", k: float = 2.0) -> "pd.DataFrame":
    """
    Añade VWAP de sesión (reset diario) + bandas a k desviaciones (std ponderada
    por volumen, acumulada dentro del día).

    df necesita: time (datetime UTC), high, low, close, volume (tick_volume).
    Devuelve copia con: vwap, vwap_std, vwap_upper, vwap_lower, vwap_slope.
    """
    out = df.copy()
    t = pd.to_datetime(out["time"])
    tp = (out["high"] + out["low"] + out["close"]) / 3.0
    vol = out["volume"].astype(float).clip(lower=1e-9)
    day = t.dt.floor("D")

    cum_vol = vol.groupby(day).cumsum()
    cum_pv = (tp * vol).groupby(day).cumsum()
    vwap = cum_pv / cum_vol

    # Varianza ponderada por volumen acumulada: E_w[tp^2] - vwap^2
    cum_pv2 = ((tp ** 2) * vol).groupby(day).cumsum()
    var = (cum_pv2 / cum_vol) - vwap ** 2
    std = np.sqrt(var.clip(lower=0))

    out["vwap"] = vwap
    out["vwap_std"] = std
    out["vwap_upper"] = vwap + k * std
    out["vwap_lower"] = vwap - k * std
    # Pendiente de VWAP por barra (para filtro de régimen: plano = rango)
    out["vwap_slope"] = vwap.diff()
    return out

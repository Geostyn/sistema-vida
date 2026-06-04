"""
Market Regime Engine — Detecta si el mercado está en tendencia o rango.

Técnicas implementadas (sin librerías externas, solo numpy/pandas):
  1. Hurst Exponent (R/S analysis) — ¿trending o mean-reverting?
  2. ADX simplificado — fuerza de la tendencia
  3. Volatility Regime — ATR actual vs histórico

Régimen de salida:
  TRENDING_UP   — ADX > 25 y precio sobre EMA50 H4
  TRENDING_DOWN — ADX > 25 y precio bajo EMA50 H4
  RANGING       — ADX < 22 o Hurst < 0.45
  HIGH_VOL      — ATR > 1.4x promedio (noticias/evento inesperado)
"""

import numpy as np
import pandas as pd
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ─── Hurst Exponent ─────────────────────────────────────────────────────────

def hurst_exponent(series: pd.Series, min_lags: int = 4, max_lags: int = 20) -> float:
    """
    Calcula el Exponente de Hurst usando el método R/S sobre retornos logarítmicos.

    H > 0.55 → trending (memoria positiva, la tendencia persiste)
    H 0.45-0.55 → random walk (no hay memoria)
    H < 0.45 → mean-reverting (precio vuelve a la media)
    """
    if len(series) < max_lags * 2:
        return 0.5  # neutral si no hay datos

    try:
        log_returns = np.log(series / series.shift(1)).dropna().values
        lags = range(min_lags, min(max_lags, len(log_returns) // 4))
        rs_values = []

        for lag in lags:
            n_windows = len(log_returns) // lag
            if n_windows < 2:
                continue
            window_rs = []
            for w in range(n_windows):
                chunk = log_returns[w * lag : (w + 1) * lag]
                if len(chunk) < 2:
                    continue
                mean_adj = chunk - chunk.mean()
                cumulative = np.cumsum(mean_adj)
                r = cumulative.max() - cumulative.min()
                s = np.std(chunk, ddof=1)
                if s > 0:
                    window_rs.append(r / s)
            if window_rs:
                rs_values.append((lag, np.mean(window_rs)))

        if len(rs_values) < 3:
            return 0.5

        lags_arr = np.log([v[0] for v in rs_values])
        rs_arr   = np.log([v[1] for v in rs_values])
        hurst, _ = np.polyfit(lags_arr, rs_arr, 1)
        return float(np.clip(hurst, 0.1, 0.9))

    except Exception as e:
        logger.debug(f"Hurst error: {e}")
        return 0.5


# ─── ADX (Average Directional Index) ────────────────────────────────────────

def calculate_adx(df: pd.DataFrame, period: int = 14) -> float:
    """
    Calcula el ADX para medir la fuerza de la tendencia.
    ADX > 25 → tendencia fuerte  |  ADX < 20 → rango / sin dirección
    No dice la dirección, solo la fuerza.
    """
    if len(df) < period + 2:
        return 20.0  # neutral

    try:
        high  = df["high"].values.astype(float)
        low   = df["low"].values.astype(float)
        close = df["close"].values.astype(float)

        # True Range
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:]  - close[:-1]),
            )
        )

        # Directional Movements
        up   = high[1:] - high[:-1]
        down = low[:-1] - low[1:]
        dm_plus  = np.where((up > down) & (up > 0), up, 0.0)
        dm_minus = np.where((down > up) & (down > 0), down, 0.0)

        # Wilder smoothing (EMA con alpha = 1/period)
        def _wilder(arr, n):
            out = np.empty(len(arr))
            out[0] = arr[:n].mean()
            alpha = 1.0 / n
            for i in range(1, len(arr)):
                out[i] = out[i - 1] * (1 - alpha) + arr[i] * alpha
            return out

        atr_s   = _wilder(tr, period)
        dmp_s   = _wilder(dm_plus, period)
        dmm_s   = _wilder(dm_minus, period)

        di_plus  = 100 * dmp_s / np.where(atr_s > 0, atr_s, 1e-9)
        di_minus = 100 * dmm_s / np.where(atr_s > 0, atr_s, 1e-9)
        dx_den   = di_plus + di_minus
        dx       = 100 * np.abs(di_plus - di_minus) / np.where(dx_den > 0, dx_den, 1e-9)
        adx      = _wilder(dx, period)

        return float(adx[-1])

    except Exception as e:
        logger.debug(f"ADX error: {e}")
        return 20.0


# ─── Volatility Regime ───────────────────────────────────────────────────────

def volatility_regime(df_h1: pd.DataFrame, window: int = 50) -> tuple[str, float]:
    """
    Compara ATR actual con ATR promedio de las últimas 'window' velas.
    Retorna (regime_str, ratio).

    ratio > 1.4 → HIGH_VOL   (noticias o evento inesperado)
    ratio < 0.7 → LOW_VOL    (mercado dormido, OBs menos confiables)
    0.7-1.4     → NORMAL_VOL
    """
    if "atr" not in df_h1.columns or len(df_h1) < window + 1:
        return "NORMAL_VOL", 1.0

    atr_series  = df_h1["atr"].dropna()
    atr_current = float(atr_series.iloc[-1])
    atr_avg     = float(atr_series.iloc[-window:].mean())

    if atr_avg == 0:
        return "NORMAL_VOL", 1.0

    ratio = atr_current / atr_avg
    if ratio > 1.4:
        return "HIGH_VOL", ratio
    if ratio < 0.7:
        return "LOW_VOL", ratio
    return "NORMAL_VOL", ratio


# ─── Clase principal ─────────────────────────────────────────────────────────

class MarketRegimeEngine:
    """
    Analiza el régimen de mercado antes de generar señales.
    Se instancia en TradingSystem y se pasa al SignalEngine.
    """

    def __init__(self, mt5_connector):
        self.mt5 = mt5_connector
        self._cache: dict = {}
        self._cache_time: datetime | None = None
        self._cache_ttl_secs = 5 * 60  # 5 minutos

    def _cache_valid(self) -> bool:
        if self._cache_time is None:
            return False
        return (datetime.now(timezone.utc) - self._cache_time).total_seconds() < self._cache_ttl_secs

    def analyze(self, symbol: str) -> dict:
        """
        Retorna el régimen actual para el símbolo dado.

        {
          "regime":                   "TRENDING_UP" | "TRENDING_DOWN" | "RANGING" | "HIGH_VOL",
          "hurst":                    float,        # 0.1–0.9
          "adx":                      float,        # 0–100
          "vol_regime":               str,          # "HIGH_VOL" | "NORMAL_VOL" | "LOW_VOL"
          "vol_ratio":                float,
          "trade_allowed":            bool,
          "min_confluences_override": float | None, # None = usar config
          "bonus_confluence":         float,        # 0.0 o +1.0
          "direction_confirmed":      bool,         # régimen confirma la dirección del trade
        }
        """
        cache_key = symbol
        if self._cache_valid() and cache_key in self._cache:
            return self._cache[cache_key]

        result = self._compute(symbol)
        self._cache[cache_key] = result
        self._cache_time       = datetime.now(timezone.utc)
        return result

    def _compute(self, symbol: str) -> dict:
        default = {
            "regime": "UNKNOWN", "hurst": 0.5, "adx": 20.0,
            "vol_regime": "NORMAL_VOL", "vol_ratio": 1.0,
            "trade_allowed": True, "min_confluences_override": None,
            "bonus_confluence": 0.0, "direction_confirmed": False,
        }

        try:
            df_h4 = self.mt5.get_rates(symbol, "H4", 200)
            df_h1 = self.mt5.get_rates(symbol, "H1", 300)

            if df_h4.empty or df_h1.empty:
                return default

            # ── Indicadores básicos ─────────────────────────────
            if "atr" not in df_h1.columns:
                from analysis.indicators import add_indicators
                df_h1 = add_indicators(df_h1)
                df_h4 = add_indicators(df_h4)

            # ── 1. Hurst sobre closes H4 ─────────────────────────
            hurst = hurst_exponent(df_h4["close"])

            # ── 2. ADX sobre H4 ──────────────────────────────────
            adx = calculate_adx(df_h4)

            # ── 3. Volatility Regime H1 ───────────────────────────
            vol_str, vol_ratio = volatility_regime(df_h1)

            # ── 4. Dirección de la tendencia (EMA50 H4) ───────────
            ema50 = float(df_h4["close"].ewm(span=50, adjust=False).mean().iloc[-1])
            price_h4 = float(df_h4["close"].iloc[-1])
            trending_up = price_h4 > ema50

            # ── Clasificar régimen ────────────────────────────────
            if vol_str == "HIGH_VOL":
                regime = "HIGH_VOL"
                trade_allowed = False
            elif adx < 18 or hurst < 0.42:
                regime = "RANGING"
                trade_allowed = True  # permitir pero con más confluencias
            elif trending_up:
                regime = "TRENDING_UP"
                trade_allowed = True
            else:
                regime = "TRENDING_DOWN"
                trade_allowed = True

            # ── Ajustar min_confluences ───────────────────────────
            if regime == "RANGING":
                min_conf_override = 5.5   # más exigente en rango
            elif regime == "HIGH_VOL":
                min_conf_override = 99.0  # bloqueado
            else:
                min_conf_override = None  # usar config normal

            result = {
                "regime":                   regime,
                "hurst":                    round(hurst, 3),
                "adx":                      round(adx, 1),
                "vol_regime":               vol_str,
                "vol_ratio":                round(vol_ratio, 2),
                "trade_allowed":            trade_allowed,
                "min_confluences_override": min_conf_override,
                "bonus_confluence":         0.0,  # se calcula en signal_engine
                "direction_confirmed":      False, # se calcula en signal_engine
            }

            logger.debug(
                f"[Régimen {symbol}] {regime} | Hurst:{hurst:.3f} ADX:{adx:.1f} "
                f"Vol:{vol_str}({vol_ratio:.2f})"
            )
            return result

        except Exception as e:
            logger.warning(f"MarketRegimeEngine error: {e}")
            return default

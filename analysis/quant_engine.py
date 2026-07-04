"""
Quant Engine — GARCH(1,1) (volatilidad prevista) + Filtro de Kalman (tendencia).

Dos técnicas cuantitativas que añaden contexto que el SMC puro no ve:

  GARCH(1,1): modela la VOLATILIDAD CONDICIONAL y prevé la de la próxima barra.
    - vol_ratio = vol prevista / vol realizada mediana → >1 = se espera más
      volatilidad de lo normal (peor para entradas de precisión; subir el listón).
    - Feature ML `garch_vol`. Guarda caché por símbolo (el fit es caro).

  Kalman (nivel + velocidad, constante-velocidad): estima la TENDENCIA suavizada
    filtrando el ruido del precio. Devuelve la pendiente (velocidad) normalizada
    por ATR → `kalman_slope` (+ alcista / − bajista). Menos whipsaw que una EMA.

Sin dependencias nuevas para Kalman (implementado a mano). GARCH usa `arch`.
"""

import time
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class QuantEngine:
    def __init__(self, config: dict | None = None):
        cfg = (config or {}).get("quant", {}) or {}
        self.garch_enabled = cfg.get("garch_enabled", True)
        self.kalman_enabled = cfg.get("kalman_enabled", True)
        self.cache_seconds = int(cfg.get("garch_cache_min", 30)) * 60
        self._garch_cache: dict = {}   # symbol -> (ts, dict)

    # ── GARCH(1,1) ──────────────────────────────────────────────────

    def garch_forecast(self, symbol: str, closes: pd.Series) -> dict:
        """
        {vol_forecast, vol_realized, vol_ratio}. vol en % de retorno por barra.
        Cacheado por símbolo (el fit GARCH es costoso ~0.1-0.3s).
        """
        if not self.garch_enabled:
            return {"vol_ratio": 1.0, "vol_forecast": 0.0, "vol_realized": 0.0}

        now = time.time()
        cached = self._garch_cache.get(symbol)
        if cached and (now - cached[0]) < self.cache_seconds:
            return cached[1]

        out = {"vol_ratio": 1.0, "vol_forecast": 0.0, "vol_realized": 0.0}
        try:
            from arch import arch_model
            c = pd.Series(closes).astype(float).dropna()
            if len(c) < 100:
                return out
            rets = 100.0 * np.log(c / c.shift(1)).dropna()
            rets = rets.tail(500)
            am = arch_model(rets, vol="Garch", p=1, q=1, mean="Zero", rescale=False)
            res = am.fit(disp="off", show_warning=False)
            fc = res.forecast(horizon=1, reindex=False)
            vol_forecast = float(np.sqrt(fc.variance.values[-1, 0]))
            # Volatilidad realizada mediana (ventana móvil de 20 barras)
            realized = rets.rolling(20).std().dropna()
            vol_realized = float(realized.tail(150).median()) if len(realized) else vol_forecast
            ratio = vol_forecast / vol_realized if vol_realized > 0 else 1.0
            out = {
                "vol_forecast": round(vol_forecast, 4),
                "vol_realized": round(vol_realized, 4),
                "vol_ratio": round(float(np.clip(ratio, 0.2, 5.0)), 3),
            }
        except Exception as e:
            logger.debug(f"GARCH no disponible: {e}")

        self._garch_cache[symbol] = (now, out)
        return out

    # ── Filtro de Kalman (nivel + velocidad) ────────────────────────

    def kalman_trend(self, closes: pd.Series, atr: float | None = None,
                     q_level=1e-3, q_vel=1e-4, r_obs=1.0) -> dict:
        """
        Kalman constante-velocidad sobre los cierres. Devuelve:
          {level, slope, slope_atr}  donde slope = velocidad estimada (precio/barra)
          y slope_atr = slope/ATR (normalizado, +alcista / −bajista).
        """
        if not self.kalman_enabled:
            return {"slope": 0.0, "slope_atr": 0.0, "level": None}

        try:
            z = pd.Series(closes).astype(float).dropna().values
            if len(z) < 20:
                return {"slope": 0.0, "slope_atr": 0.0, "level": None}
            z = z[-300:]

            # Estado x = [nivel, velocidad]; F transición; H observación
            x = np.array([z[0], 0.0])
            P = np.eye(2) * 1.0
            F = np.array([[1.0, 1.0], [0.0, 1.0]])
            H = np.array([[1.0, 0.0]])
            Q = np.array([[q_level, 0.0], [0.0, q_vel]])
            R = np.array([[r_obs]])

            for obs in z:
                # Predicción
                x = F @ x
                P = F @ P @ F.T + Q
                # Actualización
                y = obs - (H @ x)[0]
                S = (H @ P @ H.T + R)[0, 0]
                K = (P @ H.T / S).flatten()
                x = x + K * y
                P = (np.eye(2) - np.outer(K, H)) @ P

            slope = float(x[1])
            slope_atr = float(slope / atr) if atr and atr > 0 else 0.0
            return {
                "level": round(float(x[0]), 5),
                "slope": round(slope, 5),
                "slope_atr": round(float(np.clip(slope_atr, -5, 5)), 3),
            }
        except Exception as e:
            logger.debug(f"Kalman no disponible: {e}")
            return {"slope": 0.0, "slope_atr": 0.0, "level": None}

    # ── Combinado para una señal ────────────────────────────────────

    def analyze(self, symbol: str, df_h1: pd.DataFrame, atr: float | None = None) -> dict:
        """Calcula garch_vol (ratio) y kalman_slope para el motor de señales."""
        closes = df_h1["close"] if "close" in df_h1.columns else pd.Series(dtype=float)
        garch = self.garch_forecast(symbol, closes)
        kal = self.kalman_trend(closes, atr=atr)
        return {
            "garch_vol": garch["vol_ratio"],          # 1.0 = normal, >1.3 = alta vol prevista
            "garch_forecast": garch["vol_forecast"],
            "kalman_slope": kal["slope_atr"],         # +alcista / −bajista (en ATR/barra)
        }

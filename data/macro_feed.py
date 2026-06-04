"""
Macro Feed — Datos macroeconómicos via yfinance (gratis).
Correlaciones clave para XAUUSD:
  ^DXY   → DXY sube = oro baja (correlacion -0.85 promedio)
  ^TNX   → Rendimiento bono 10Y: sube = oro baja (coste de oportunidad)
  ^VIX   → Volatilidad/miedo: sube = oro sube (activo refugio)
  ^GSPC  → S&P 500: cae en crisis = oro sube
  EURUSD → Correlacion positiva con oro (ambos vs USD)
"""

import os
import yfinance as yf
import pandas as pd
import logging
import urllib3
from datetime import datetime, timezone

# Desactivar verificacion SSL (certificados rotos en Windows — solo uso local)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
os.environ["CURL_CA_BUNDLE"]     = ""
os.environ["REQUESTS_CA_BUNDLE"] = ""

logger = logging.getLogger(__name__)

# Sesión con SSL desactivado para yfinance
def _make_yf_session():
    try:
        from curl_cffi import requests as cffi
        return cffi.Session(verify=False)
    except Exception:
        import requests as req
        s = req.Session()
        s.verify = False
        return s

_YF_SESSION = _make_yf_session()

# Símbolos macro en Yahoo Finance
SYMBOLS = {
    "dxy":   "^DXY",    # US Dollar Index
    "bonds": "^TNX",    # US 10Y Treasury Yield
    "vix":   "^VIX",    # CBOE Volatility Index
    "spx":   "^GSPC",   # S&P 500
}

CACHE_MINUTES = 30  # Refrescar cada 30 min — Yahoo Finance tiene rate limit estricto


class MacroFeed:
    def __init__(self):
        self._cache: dict = {}
        self._cache_time: datetime | None = None

    def _cache_valid(self) -> bool:
        if self._cache_time is None:
            return False
        return (datetime.now(timezone.utc) - self._cache_time).total_seconds() < CACHE_MINUTES * 60

    def fetch_all(self, force: bool = False) -> dict:
        """
        Descarga todos los datos macro. Usa cache de 5 minutos.
        Returns dict con DataFrames OHLCV para cada símbolo.
        """
        if not force and self._cache_valid() and self._cache:
            return self._cache

        data = {}
        for name, ticker in SYMBOLS.items():
            try:
                df = yf.Ticker(ticker, session=_YF_SESSION).history(period="5d", interval="1h", auto_adjust=True)
                if not df.empty:
                    df.index = pd.to_datetime(df.index, utc=True)
                    df.columns = [c.lower() for c in df.columns]
                    data[name] = df
                    logger.debug(f"Macro {name} ({ticker}): {len(df)} barras")
            except Exception as e:
                logger.warning(f"Error descargando {ticker}: {e}")

        self._cache = data
        self._cache_time = datetime.now(timezone.utc)
        return data

    def get_macro_bias(self) -> dict:
        """
        Analiza el contexto macro y devuelve sesgos direccionales para XAUUSD.

        Returns dict con:
          dxy_trend:    "BEARISH" | "NEUTRAL" | "BULLISH"   (bearish DXY = alcista oro)
          yields_trend: "BEARISH" | "NEUTRAL" | "BULLISH"   (bearish yields = alcista oro)
          risk_mood:    "RISK_OFF" | "NEUTRAL" | "RISK_ON"  (risk-off = alcista oro)
          gold_bias:    "BULLISH" | "NEUTRAL" | "BEARISH"   (sesgo macro total para oro)
          score:        float entre -1 (muy bajista) y +1 (muy alcista) para oro
          details:      dict con valores de cada indicador
        """
        data  = self.fetch_all()
        score = 0
        votes = 0
        details = {}

        # ── 1. DXY — correlacion inversa con oro ────────────────
        dxy_trend = "NEUTRAL"
        if "dxy" in data and len(data["dxy"]) >= 20:
            dxy  = data["dxy"]["close"]
            ema5  = dxy.ewm(span=5,  adjust=False).mean().iloc[-1]
            ema20 = dxy.ewm(span=20, adjust=False).mean().iloc[-1]
            dxy_last = float(dxy.iloc[-1])
            dxy_prev = float(dxy.iloc[-6])  # hace 6h
            dxy_chg  = (dxy_last - dxy_prev) / dxy_prev * 100

            if ema5 < ema20 and dxy_chg < -0.1:
                dxy_trend = "BEARISH"   # DXY cae → oro alcista
                score += 1
            elif ema5 > ema20 and dxy_chg > 0.1:
                dxy_trend = "BULLISH"   # DXY sube → oro bajista
                score -= 1
            votes += 1
            details["dxy"] = {"valor": round(dxy_last, 2), "cambio_6h": round(dxy_chg, 3), "ema_trend": dxy_trend}

        # ── 2. Rendimiento bonos 10Y — correlacion inversa ────────
        yields_trend = "NEUTRAL"
        if "bonds" in data and len(data["bonds"]) >= 10:
            yields  = data["bonds"]["close"]
            y_last  = float(yields.iloc[-1])
            y_prev  = float(yields.iloc[-6])
            y_chg   = y_last - y_prev  # en puntos porcentuales

            if y_chg < -0.03:
                yields_trend = "BEARISH"   # Yields caen → oro alcista
                score += 0.7
            elif y_chg > 0.03:
                yields_trend = "BULLISH"   # Yields suben → oro bajista
                score -= 0.7
            votes += 1
            details["bonds_10y"] = {"valor": round(y_last, 3), "cambio_6h": round(y_chg, 4), "trend": yields_trend}

        # ── 3. VIX — miedo/refugio ────────────────────────────────
        risk_mood = "NEUTRAL"
        if "vix" in data and len(data["vix"]) >= 10:
            vix     = data["vix"]["close"]
            vix_now = float(vix.iloc[-1])
            vix_avg = float(vix.tail(20).mean())

            if vix_now > vix_avg * 1.10:
                risk_mood = "RISK_OFF"   # VIX elevado → oro alcista (refugio)
                score += 0.8
            elif vix_now < vix_avg * 0.90:
                risk_mood = "RISK_ON"    # VIX bajo → apetito riesgo → oro neutral/bajista
                score -= 0.4
            votes += 1
            details["vix"] = {"valor": round(vix_now, 2), "media_20": round(vix_avg, 2), "mood": risk_mood}

        # ── 4. S&P 500 — sentimiento general ──────────────────────
        spx_trend = "NEUTRAL"
        if "spx" in data and len(data["spx"]) >= 20:
            spx     = data["spx"]["close"]
            spx_now = float(spx.iloc[-1])
            spx_24h = float(spx.iloc[-25]) if len(spx) >= 25 else float(spx.iloc[0])
            spx_chg = (spx_now - spx_24h) / spx_24h * 100

            if spx_chg < -1.5:
                spx_trend = "FALLING"    # SPX cae fuerte → riesgo off → oro alcista
                score += 0.5
            elif spx_chg > 1.5:
                spx_trend = "RISING"
                score -= 0.3
            details["spx"] = {"valor": round(spx_now, 0), "cambio_24h": round(spx_chg, 2), "trend": spx_trend}

        # ── Sesgo macro final ─────────────────────────────────────
        if votes == 0:
            gold_bias = "NEUTRAL"
            norm_score = 0.0
        else:
            norm_score = score / votes
            if norm_score >= 0.3:
                gold_bias = "BULLISH"
            elif norm_score <= -0.3:
                gold_bias = "BEARISH"
            else:
                gold_bias = "NEUTRAL"

        return {
            "dxy_trend":    dxy_trend,
            "yields_trend": yields_trend,
            "risk_mood":    risk_mood,
            "gold_bias":    gold_bias,
            "score":        round(norm_score, 3),
            "details":      details,
            "timestamp":    datetime.now(timezone.utc).isoformat(),
        }

    def get_dxy_value(self) -> float | None:
        """Devuelve el último valor del DXY."""
        data = self.fetch_all()
        if "dxy" in data and not data["dxy"].empty:
            return float(data["dxy"]["close"].iloc[-1])
        return None

    def get_dxy_chart_data(self, interval: str = "5m", period: str = "1d") -> pd.DataFrame:
        """
        Retorna velas del DXY para el dashboard (por defecto M5, últimas 24h).
        yfinance soporta interval='5m','15m','1h' para period corto.
        Se usa ^DXY (US Dollar Index — ICE).
        Retorna DataFrame con columnas: time, open, high, low, close, volume
        """
        try:
            df = yf.Ticker("^DXY", session=_YF_SESSION).history(
                period=period, interval=interval, auto_adjust=True
            )
            if df.empty:
                return pd.DataFrame()
            df.index   = pd.to_datetime(df.index, utc=True)
            df.columns = [c.lower() for c in df.columns]
            df         = df.reset_index().rename(columns={"datetime": "time", "index": "time"})
            if "time" not in df.columns and "date" in df.columns:
                df = df.rename(columns={"date": "time"})
            # Añadir EMA20
            df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
            return df
        except Exception as e:
            logger.warning(f"get_dxy_chart_data error: {e}")
            return pd.DataFrame()

    def get_macro_summary(self) -> dict:
        """
        Resumen compacto para el dashboard: DXY, Yields, VIX con valores y tendencias.
        """
        data = self.fetch_all()
        summary = {}

        if "dxy" in data and not data["dxy"].empty:
            dxy = data["dxy"]["close"]
            summary["dxy"] = {
                "value":   round(float(dxy.iloc[-1]), 3),
                "change":  round(float(dxy.iloc[-1] - dxy.iloc[-6]), 3),
                "pct_chg": round(float((dxy.iloc[-1] - dxy.iloc[-6]) / dxy.iloc[-6] * 100), 3),
                "trend":   "↑" if dxy.iloc[-1] > dxy.iloc[-6] else "↓",
                "impact":  "bearish_gold" if dxy.iloc[-1] > dxy.iloc[-6] else "bullish_gold",
            }

        if "bonds" in data and not data["bonds"].empty:
            tnx = data["bonds"]["close"]
            summary["yields_10y"] = {
                "value":   round(float(tnx.iloc[-1]), 3),
                "change":  round(float(tnx.iloc[-1] - tnx.iloc[-6]), 4),
                "trend":   "↑" if tnx.iloc[-1] > tnx.iloc[-6] else "↓",
                "impact":  "bearish_gold" if tnx.iloc[-1] > tnx.iloc[-6] else "bullish_gold",
            }

        if "vix" in data and not data["vix"].empty:
            vix = data["vix"]["close"]
            vix_avg = float(vix.tail(20).mean())
            summary["vix"] = {
                "value":   round(float(vix.iloc[-1]), 2),
                "avg20":   round(vix_avg, 2),
                "regime":  "RISK_OFF" if float(vix.iloc[-1]) > vix_avg * 1.1 else "RISK_ON",
                "impact":  "bullish_gold" if float(vix.iloc[-1]) > vix_avg * 1.1 else "neutral",
            }

        return summary

    def test_connection(self) -> dict:
        """Verifica si yfinance puede descargar datos macro. Útil para diagnóstico."""
        results = {}
        for name, ticker in SYMBOLS.items():
            try:
                df = yf.Ticker(ticker, session=_YF_SESSION).history(period="2d", interval="1h", auto_adjust=True)
                results[name] = "OK" if not df.empty else "EMPTY"
            except Exception as e:
                results[name] = f"ERROR: {e}"
        return results

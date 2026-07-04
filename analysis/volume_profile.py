"""
Volume Profile Engine — Perfil de volumen desde datos COMEX (GC=F).

Usa datos de Gold Futures (CME COMEX) via yfinance porque tienen volumen
institucional real de exchange, a diferencia del tick_volume del broker MT5
(que solo refleja actividad del broker, no el mercado real).

Correlación GC=F / XAUUSD spot: ~0.92

Niveles calculados:
  POC (Point of Control) — precio con mayor volumen en el período
  VAH (Value Area High)  — borde superior del 70% de volumen
  VAL (Value Area Low)   — borde inferior del 70% de volumen
"""

import numpy as np
import pandas as pd
import logging
import os
import json
import urllib3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
os.environ["CURL_CA_BUNDLE"]     = ""
os.environ["REQUESTS_CA_BUNDLE"] = ""


def _get_yf_session():
    try:
        from curl_cffi import requests as cffi
        return cffi.Session(verify=False)
    except Exception:
        import requests as req
        s = req.Session()
        s.verify = False
        return s


_YF_SESSION = _get_yf_session()

CACHE_MINUTES = 60


class VolumeProfileEngine:
    """
    Calcula y cachea el perfil de volumen de XAUUSD usando datos COMEX (GC=F).
    """

    def __init__(self, config: dict):
        self.config      = config.get("comex", {})
        self.ticker      = self.config.get("ticker", "GC=F")
        self.cache_min   = int(self.config.get("cache_minutes", CACHE_MINUTES))
        self.va_pct      = float(self.config.get("value_area_pct", 0.70))
        self._cache      = None
        self._cache_time = None
        # Caché en disco: sobrevive reinicios y evita depender de yfinance al
        # arrancar (mismo patrón que intermarket_feed).
        self._cache_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "logs", "vp_comex_cache.json")
        self._load_disk_cache()

    def _cache_valid(self) -> bool:
        if self._cache_time is None:
            return False
        age = (datetime.now(timezone.utc) - self._cache_time).total_seconds()
        return age < self.cache_min * 60

    def _load_disk_cache(self):
        """Carga el último perfil guardado en disco (si existe y es legible)."""
        try:
            if not os.path.exists(self._cache_file):
                return
            with open(self._cache_file, "r", encoding="utf-8") as f:
                blob = json.load(f)
            prof = blob.get("profile")
            ts   = blob.get("cache_time")
            if prof and ts:
                self._cache      = prof
                self._cache_time = datetime.fromisoformat(ts)
                logger.info("VP COMEX: caché cargada de disco (arranque sin yfinance)")
        except Exception as e:
            logger.debug(f"VP COMEX: no se pudo cargar caché de disco: {e}")

    def _save_disk_cache(self, profile: dict):
        try:
            os.makedirs(os.path.dirname(self._cache_file), exist_ok=True)
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump({"profile": profile,
                           "cache_time": datetime.now(timezone.utc).isoformat()},
                          f, default=str)
        except Exception as e:
            logger.debug(f"VP COMEX: no se pudo guardar caché en disco: {e}")

    def _fetch_comex_data(self) -> pd.DataFrame:
        """Descarga 30 días de datos H1 de GC=F (Gold Futures CME)."""
        try:
            from data.yf_safe import safe_history
            df = safe_history(self.ticker, session=_YF_SESSION,
                              period="30d", interval="1h", auto_adjust=True)
            if df.empty:
                return pd.DataFrame()
            df.index = pd.to_datetime(df.index, utc=True)
            df.columns = [c.lower() for c in df.columns]
            return df[["open", "high", "low", "close", "volume"]].dropna()
        except Exception as e:
            logger.warning(f"COMEX data error ({self.ticker}): {e}")
            return pd.DataFrame()

    def _calculate_profile(self, df: pd.DataFrame, n_levels: int = 200) -> dict:
        """
        Construye el perfil de volumen distribuyendo cada barra entre sus niveles H-L.
        Retorna POC, VAH, VAL con coordenadas de precio reales.
        """
        price_min = float(df["low"].min())
        price_max = float(df["high"].max())
        if price_min >= price_max:
            return {}

        bin_size      = (price_max - price_min) / n_levels
        vol_by_level  = np.zeros(n_levels)

        for _, row in df.iterrows():
            b_low  = max(0, int((row["low"]  - price_min) / bin_size))
            b_high = min(n_levels - 1, int((row["high"] - price_min) / bin_size))
            span   = max(1, b_high - b_low + 1)
            vol_pp = row["volume"] / span
            vol_by_level[b_low:b_high + 1] += vol_pp

        # POC
        poc_bin   = int(np.argmax(vol_by_level))
        poc_price = price_min + (poc_bin + 0.5) * bin_size

        # Value Area expandiendo desde el POC
        total_vol  = float(vol_by_level.sum())
        if total_vol == 0:
            return {}
        target_vol = total_vol * self.va_pct

        va_vol   = float(vol_by_level[poc_bin])
        vah_bin  = poc_bin
        val_bin  = poc_bin
        up, down = poc_bin + 1, poc_bin - 1

        while va_vol < target_vol:
            add_up   = float(vol_by_level[up])   if up   < n_levels else 0.0
            add_down = float(vol_by_level[down]) if down >= 0        else 0.0
            if add_up == 0 and add_down == 0:
                break
            if add_up >= add_down and up < n_levels:
                va_vol += add_up; vah_bin = up; up += 1
            elif down >= 0:
                va_vol += add_down; val_bin = down; down -= 1
            else:
                break

        vah = price_min + (vah_bin + 0.5) * bin_size
        val = price_min + (val_bin + 0.5) * bin_size

        return {
            "poc":       round(poc_price, 2),
            "vah":       round(vah, 2),
            "val":       round(val, 2),
            "bin_size":  round(bin_size, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_levels(self, spot_price: float = None) -> dict:
        """
        Retorna POC, VAH, VAL actuales.
        Si se provee spot_price, escala los niveles COMEX al precio spot de XAUUSD
        usando el ratio actual entre los dos mercados.

        Returns:
            {poc, vah, val, bin_size, ratio, source, timestamp}
            o {} si no hay datos.
        """
        if self._cache_valid() and self._cache:
            result = dict(self._cache)
            if spot_price and result.get("poc"):
                result = self._apply_spot_ratio(result, spot_price)
            return result

        df = self._fetch_comex_data()
        if df.empty:
            logger.warning("VP: sin datos COMEX — usando cache anterior")
            return self._cache or {}

        profile = self._calculate_profile(df)
        if not profile:
            return self._cache or {}

        self._cache      = profile
        self._cache_time = datetime.now(timezone.utc)
        self._save_disk_cache(profile)

        logger.info(
            f"VP COMEX actualizado | POC:{profile['poc']} "
            f"VAH:{profile['vah']} VAL:{profile['val']}"
        )

        result = dict(profile)
        if spot_price and result.get("poc"):
            result = self._apply_spot_ratio(result, spot_price)
        return result

    def _apply_spot_ratio(self, profile: dict, spot_price: float) -> dict:
        """Escala niveles COMEX → XAUUSD spot usando el ratio actual."""
        poc = profile.get("poc", 0)
        if not poc or not spot_price:
            return profile
        ratio = spot_price / poc
        scaled = dict(profile)
        for key in ("poc", "vah", "val"):
            if scaled.get(key):
                scaled[key] = round(scaled[key] * ratio, 2)
        scaled["ratio"]  = round(ratio, 4)
        scaled["source"] = "COMEX-scaled"
        return scaled

    def get_confluence(self, price: float, direction: str, atr: float,
                       spot_price: float = None) -> tuple[float, str]:
        """
        Calcula la confluencia de volume profile para el setup actual.

        Returns:
            (score, description) donde score es el peso a sumar.
        """
        levels = self.get_levels(spot_price=spot_price)
        if not levels or not levels.get("poc"):
            return 0.0, ""

        poc = levels["poc"]
        vah = levels["vah"]
        val = levels["val"]
        tol = atr * 1.5  # tolerancia para "cerca de un nivel"

        if direction == "BUY":
            # Precio dentro de la Value Area (entre VAL y POC) = zona institucional de compra
            if val - tol <= price <= poc + tol:
                return 1.5, f"VP: precio en Value Area BUY (VAL:{val:.0f}–POC:{poc:.0f})"
            # Precio bajo VAL = zona de descuento, retorno probable hacia POC
            if price < val - tol:
                return 1.0, f"VP: precio bajo VAL {val:.0f} — descuento COMEX"
        else:  # SELL
            # Precio dentro de la Value Area (entre POC y VAH) = zona institucional de venta
            if poc - tol <= price <= vah + tol:
                return 1.5, f"VP: precio en Value Area SELL (POC:{poc:.0f}–VAH:{vah:.0f})"
            # Precio sobre VAH = zona premium, retorno probable
            if price > vah + tol:
                return 1.0, f"VP: precio sobre VAH {vah:.0f} — premium COMEX"

        return 0.0, ""

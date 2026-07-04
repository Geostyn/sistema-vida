"""
Intermarket Feed — Datos cross-asset GRATIS que usan los programas de pago.

Aporta la ventaja informativa LEGAL que el retail ignora (no "insider"):

  1. RENDIMIENTOS REALES 10Y (FRED DFII10) — el driver #1 del precio del oro.
     Reales ↓ ⇒ oro ↑ (baja el coste de oportunidad de tener un activo sin cupón).
     Fuente primaria: FRED (gratis, sin rate limit). Fallback: ETF TIP (yfinance).
     Clave: la única señal que NO deriva del precio del oro → rompe la circularidad
     de las confluencias técnicas (que en tendencia fuerte se apilan en falso).

  2. POSICIONAMIENTO COT (CFTC, legacy futures) — qué hacen los grandes especuladores
     en futuros de oro COMEX. Net non-commercial + percentil 3 años (extremos =
     contrarian) + cambio semanal (momentum del dinero institucional). Semanal.

  3. RATIO ORO/PLATA — z-score; extremos anticipan reversiones del metal.

  4. CESTA DE RIESGO — BTC (oro digital), cobre y petróleo (reflación/termómetro macro).

Diseño: caché AGRESIVA en disco (logs/intermarket_cache.json) para sobrevivir al
rate-limit de Yahoo Finance y a cortes de red. Degradación elegante: si una fuente
falla, usa caché o se omite — NUNCA rompe la generación de señales.
"""

import os
import json
import logging
import io
import zipfile
from datetime import datetime, timezone

import ssl
import urllib3
import numpy as np
import pandas as pd

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Certificados rotos en Windows: fredapi usa urllib (urlopen) y no respeta
# verify=False → bypass global del contexto SSL (bot local, mismo patron que
# el resto del proyecto: verify=False en requests/yfinance).
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except Exception:
    pass

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(_PROJECT_ROOT, "logs", "intermarket_cache.json")

FAIL_COOLDOWN_MIN = 5   # Tras un fallo de una fuente, NO reintentarla hasta pasados
                        # 5 min (patrón macro_feed: evita refetch cada ciclo en caídas)

# Sesión yfinance con SSL desactivado (mismo patrón que macro_feed)
def _make_yf_session():
    try:
        from curl_cffi import requests as cffi
        return cffi.Session(verify=False)
    except Exception:
        import requests as req
        s = req.Session()
        s.verify = False
        return s


class IntermarketFeed:
    def __init__(self, config: dict, mt5_connector=None):
        self.config = config
        im = config.get("intermarket", {}) or {}
        self.enabled = im.get("enabled", True)
        self.fred_key = (config.get("apis", {}) or {}).get("fred_key", "") or ""
        self.mt5 = mt5_connector
        self.max_confluence = float(im.get("max_confluence", 2.5))
        self._ttl = {
            "real_yields": int(im.get("real_yields_cache_min", 360)) * 60,
            "cot":         int(im.get("cot_cache_min", 720)) * 60,
            "ratio":       int(im.get("ratio_cache_min", 120)) * 60,
            "risk":        int(im.get("risk_cache_min", 120)) * 60,
        }
        self._yf = _make_yf_session()
        self._cache = self._load_cache()
        self._fail_ts: dict = {}  # key → datetime del último fallo (cooldown)

    # ── Caché en disco ──────────────────────────────────────────────

    def _load_cache(self) -> dict:
        if os.path.exists(CACHE_PATH):
            try:
                with open(CACHE_PATH, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_cache(self):
        try:
            os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._cache, f)
        except Exception as e:
            logger.debug(f"No se pudo guardar caché intermarket: {e}")

    def _cached(self, key: str):
        """Devuelve el valor cacheado si sigue fresco, si no None."""
        entry = self._cache.get(key)
        if not entry:
            return None
        try:
            ts = datetime.fromisoformat(entry["_ts"])
            if (datetime.now(timezone.utc) - ts).total_seconds() < self._ttl.get(key, 3600):
                return entry["data"]
        except Exception:
            pass
        return None

    def _store(self, key: str, data):
        self._cache[key] = {"_ts": datetime.now(timezone.utc).isoformat(), "data": data}
        self._save_cache()

    def _cached_stale(self, key: str):
        """Caché aunque esté vieja (último recurso si la fuente falla)."""
        entry = self._cache.get(key)
        return entry["data"] if entry else None

    def _in_fail_cooldown(self, key: str) -> bool:
        ts = self._fail_ts.get(key)
        return ts is not None and \
            (datetime.now(timezone.utc) - ts).total_seconds() < FAIL_COOLDOWN_MIN * 60

    def _mark_fail(self, key: str):
        self._fail_ts[key] = datetime.now(timezone.utc)

    # ── 1. Rendimientos reales 10Y ──────────────────────────────────

    def get_real_yields(self) -> dict:
        """
        Real 10Y yield (DFII10). trend BEARISH = reales cayendo = ALCISTA oro.
        Returns {value, change_5d, trend, gold_impact, source}
        """
        cached = self._cached("real_yields")
        if cached:
            return cached
        if self._in_fail_cooldown("real_yields"):
            return self._cached_stale("real_yields") or {
                "trend": "NEUTRAL", "gold_impact": 0.0, "source": "sin datos"}

        data = None
        # --- Primario: FRED DFII10 (real 10Y) ---
        if self.fred_key:
            try:
                from fredapi import Fred
                fred = Fred(api_key=self.fred_key)
                s = fred.get_series("DFII10").dropna()
                if len(s) >= 6:
                    last = float(s.iloc[-1])
                    prev = float(s.iloc[-6])
                    data = self._yield_dict(last, last - prev, "FRED DFII10 (real 10Y)")
            except Exception as e:
                logger.debug(f"FRED real yields no disponible: {e}")

        # --- Fallback: ETF TIP (inverso a reales) via yfinance ---
        if data is None:
            try:
                from data.yf_safe import safe_history
                df = safe_history("TIP", session=self._yf, period="1mo", interval="1d")
                if not df.empty and len(df) >= 6:
                    c = df["Close"]
                    # TIP sube ⇒ reales bajan. Invertimos el cambio para el "trend de reales".
                    pct = (float(c.iloc[-1]) - float(c.iloc[-6])) / float(c.iloc[-6]) * 100
                    data = self._yield_dict(None, -pct, "ETF TIP proxy (yfinance)")
            except Exception as e:
                logger.debug(f"TIP proxy no disponible: {e}")

        if data is None:
            self._mark_fail("real_yields")
            return self._cached_stale("real_yields") or {
                "trend": "NEUTRAL", "gold_impact": 0.0, "source": "sin datos"}

        self._store("real_yields", data)
        return data

    @staticmethod
    def _yield_dict(value, change_5d, source) -> dict:
        # change_5d en puntos (FRED) o en %-invertido (proxy). El signo manda.
        if change_5d <= -0.03:
            trend, impact = "BEARISH", +1.0      # reales bajan → oro alcista
        elif change_5d >= 0.03:
            trend, impact = "BULLISH", -1.0      # reales suben → oro bajista
        else:
            trend, impact = "NEUTRAL", 0.0
        return {
            "value": round(value, 3) if value is not None else None,
            "change_5d": round(float(change_5d), 4),
            "trend": trend,
            "gold_impact": impact,
            "source": source,
        }

    # ── 2. Posicionamiento COT (CFTC) ───────────────────────────────

    def get_cot_gold(self) -> dict:
        """
        Net non-commercial en oro COMEX + percentil ~3 años + cambio semanal.
        Returns {net, net_pct_oi, percentile, weekly_change, extreme, gold_impact, asof}
        """
        cached = self._cached("cot")
        if cached:
            return cached
        if self._in_fail_cooldown("cot"):
            return self._cached_stale("cot") or {
                "extreme": "NORMAL", "gold_impact": 0.0, "asof": "sin datos"}

        try:
            import requests
            frames = []
            year = datetime.now(timezone.utc).year
            for y in (year, year - 1, year - 2):
                url = f"https://www.cftc.gov/files/dea/history/deacot{y}.zip"
                try:
                    # timeout corto: 3 descargas síncronas en el ciclo del bot —
                    # con 45 s un CFTC caído bloqueaba el ciclo ~135 s
                    r = requests.get(url, verify=False, timeout=15)
                    if r.status_code != 200:
                        continue
                    zf = zipfile.ZipFile(io.BytesIO(r.content))
                    df = pd.read_csv(zf.open(zf.namelist()[0]), low_memory=False)
                    frames.append(df)
                except Exception as e:
                    logger.debug(f"COT {y} no disponible: {e}")
            if not frames:
                raise RuntimeError("sin ficheros COT")

            allcot = pd.concat(frames, ignore_index=True)
            namecol = "Market and Exchange Names"
            datecol = "As of Date in Form YYYY-MM-DD"
            longc = "Noncommercial Positions-Long (All)"
            shortc = "Noncommercial Positions-Short (All)"
            # Match EXACTO: "GOLD - COMMODITY EXCHANGE INC." (NO "MICRO GOLD...")
            name_norm = allcot[namecol].astype(str).str.strip().str.upper()
            g = allcot[name_norm == "GOLD - COMMODITY EXCHANGE INC."].copy()
            g[datecol] = pd.to_datetime(g[datecol], errors="coerce")
            g = g.dropna(subset=[datecol]).drop_duplicates(
                subset=[datecol]).sort_values(datecol)
            g["net"] = g[longc] - g[shortc]
            net_series = g["net"].astype(float)

            last_net = float(net_series.iloc[-1])
            prev_net = float(net_series.iloc[-2]) if len(net_series) >= 2 else last_net
            # Percentil del net actual sobre todo el histórico disponible (~3 años)
            pct_rank = float((net_series <= last_net).mean())
            extreme = "CROWDED_LONG" if pct_rank >= 0.85 else \
                      ("CROWDED_SHORT" if pct_rank <= 0.15 else "NORMAL")
            # Impacto: extremos = contrarian; cambio semanal = momentum (peso menor)
            impact = 0.0
            if extreme == "CROWDED_LONG":
                impact -= 0.5     # demasiados largos → riesgo de corrección bajista
            elif extreme == "CROWDED_SHORT":
                impact += 0.5     # demasiados cortos → posible squeeze alcista
            if last_net > prev_net:
                impact += 0.25    # institucionales añadiendo largos
            else:
                impact -= 0.25
            impact = float(np.clip(impact, -1.0, 1.0))

            data = {
                "net": int(last_net),
                "weekly_change": int(last_net - prev_net),
                "percentile": round(pct_rank, 2),
                "extreme": extreme,
                "gold_impact": impact,
                "asof": str(g[datecol].iloc[-1].date()),
                "weeks": int(len(net_series)),
            }
            self._store("cot", data)
            return data
        except Exception as e:
            logger.debug(f"COT no disponible: {e}")
            self._mark_fail("cot")
            return self._cached_stale("cot") or {
                "extreme": "NORMAL", "gold_impact": 0.0, "asof": "sin datos"}

    # ── 3. Ratio oro/plata ──────────────────────────────────────────

    def get_gold_silver(self) -> dict:
        """
        Ratio oro/plata + z-score sobre ~100 cierres diarios.
        Plata: MT5 XAGUSD si existe, si no SI=F (yfinance).
        """
        cached = self._cached("ratio")
        if cached:
            return cached
        if self._in_fail_cooldown("ratio"):
            return self._cached_stale("ratio") or {"signal": "NEUTRAL", "gold_impact": 0.0}

        gold = silver = None
        gold_hist = silver_hist = None
        # Oro y plata desde MT5 (sin rate limit) si el broker los tiene
        if self.mt5 is not None:
            try:
                dg = self.mt5.get_rates("XAUUSD", "D1", 120)
                ds = self.mt5.get_rates("XAGUSD", "D1", 120)
                if not dg.empty and ds is not None and not ds.empty:
                    gold_hist = dg.set_index("time")["close"]
                    silver_hist = ds.set_index("time")["close"]
            except Exception as e:
                logger.debug(f"MT5 oro/plata no disponible: {e}")

        # Fallback yfinance (GC=F / SI=F)
        if gold_hist is None or silver_hist is None:
            try:
                from data.yf_safe import safe_history
                dg = safe_history("GC=F", session=self._yf, period="6mo", interval="1d")
                ds = safe_history("SI=F", session=self._yf, period="6mo", interval="1d")
                if not dg.empty and not ds.empty:
                    gold_hist = dg["Close"]
                    silver_hist = ds["Close"]
            except Exception as e:
                logger.debug(f"yfinance oro/plata no disponible: {e}")

        if gold_hist is None or silver_hist is None:
            self._mark_fail("ratio")
            return self._cached_stale("ratio") or {"signal": "NEUTRAL", "gold_impact": 0.0}

        try:
            ratio = (gold_hist.values / silver_hist.values[-len(gold_hist):]) \
                if len(silver_hist) >= len(gold_hist) else \
                (gold_hist.values[-len(silver_hist):] / silver_hist.values)
            ratio = pd.Series(ratio).dropna()
            last = float(ratio.iloc[-1])
            mean = float(ratio.tail(100).mean())
            std = float(ratio.tail(100).std()) or 1.0
            z = (last - mean) / std
            # Ratio alto = plata barata / risk-off (oro relativamente fuerte).
            # Como señal direccional para el oro es débil; peso pequeño.
            signal = "RATIO_HIGH" if z >= 2 else ("RATIO_LOW" if z <= -2 else "NEUTRAL")
            data = {"ratio": round(last, 2), "zscore": round(float(z), 2),
                    "signal": signal, "gold_impact": 0.0}
            self._store("ratio", data)
            return data
        except Exception as e:
            logger.debug(f"Ratio oro/plata error: {e}")
            return {"signal": "NEUTRAL", "gold_impact": 0.0}

    # ── 4. Cesta de riesgo (BTC / cobre / petróleo) ─────────────────

    def get_risk_basket(self) -> dict:
        cached = self._cached("risk")
        if cached:
            return cached
        if self._in_fail_cooldown("risk"):
            return self._cached_stale("risk") or {"gold_impact": 0.0}
        out = {"gold_impact": 0.0}
        try:
            from data.yf_safe import safe_history
            tickers = {"btc": "BTC-USD", "copper": "HG=F", "oil": "CL=F"}
            moves = {}
            for name, tk in tickers.items():
                try:
                    df = safe_history(tk, session=self._yf, period="5d", interval="1d")
                    if not df.empty and len(df) >= 2:
                        c = df["Close"]
                        moves[name] = round(float((c.iloc[-1] - c.iloc[-2]) / c.iloc[-2] * 100), 2)
                except Exception:
                    pass
            # Reflación fuerte (cobre+petróleo subiendo) suele acompañar risk-on:
            # señal débil ligeramente bajista para el oro refugio. Peso pequeño.
            reflation = sum(v for k, v in moves.items() if k in ("copper", "oil"))
            impact = 0.0
            if reflation <= -3:
                impact += 0.25   # caída de materias → riesgo off → oro
            elif reflation >= 3:
                impact -= 0.15
            out = {"moves": moves, "gold_impact": float(impact)}
            self._store("risk", out)
        except Exception as e:
            logger.debug(f"Risk basket no disponible: {e}")
            self._mark_fail("risk")
            return self._cached_stale("risk") or out
        return out

    # ── Score combinado para una señal ──────────────────────────────

    def get_intermarket_score(self, direction: str) -> dict:
        """
        Combina las 4 fuentes en un score de alineación con la dirección dada.

        Returns:
          {
            gold_bias_score: float [-1..+1]   (+ alcista oro),
            aligned_score:   float [0..1]     (cuánto apoya la dirección de la señal),
            confluence:      float [0..max]   (bonus para sumar a las confluencias),
            details:         list[str],
            real_yields, cot, ratio, risk:    dicts crudos (para features ML / Telegram)
          }
        """
        if not self.enabled:
            return {"gold_bias_score": 0.0, "aligned_score": 0.0,
                    "confluence": 0.0, "details": []}

        ry = self.get_real_yields()
        cot = self.get_cot_gold()
        ratio = self.get_gold_silver()
        risk = self.get_risk_basket()

        # Pesos: reales mandan (driver #1), COT medio, ratio/riesgo bajos
        gold_score = (
            ry.get("gold_impact", 0.0) * 0.55 +
            cot.get("gold_impact", 0.0) * 0.30 +
            risk.get("gold_impact", 0.0) * 0.15
        )
        gold_score = float(np.clip(gold_score, -1.0, 1.0))

        # Alineación con la dirección de la señal
        signed = gold_score if direction == "BUY" else -gold_score
        aligned_score = max(0.0, signed)  # 0 si va en contra, >0 si a favor
        confluence = round(min(aligned_score * self.max_confluence, self.max_confluence), 2)

        details = []
        if ry.get("trend") != "NEUTRAL":
            details.append(f"Reales 10Y {ry['trend']} ({ry.get('source','')})")
        if cot.get("extreme") not in (None, "NORMAL"):
            details.append(f"COT {cot['extreme']} (pct {cot.get('percentile')})")
        if ratio.get("signal") not in (None, "NEUTRAL"):
            details.append(f"Oro/Plata {ratio['signal']} z={ratio.get('zscore')}")

        return {
            "gold_bias_score": round(gold_score, 3),
            "aligned_score": round(aligned_score, 3),
            "confluence": confluence,
            "details": details,
            "real_yields": ry,
            "cot": cot,
            "ratio": ratio,
            "risk": risk,
        }

    def test_connection(self) -> dict:
        """Diagnóstico rápido de cada fuente."""
        return {
            "real_yields": self.get_real_yields(),
            "cot": {k: v for k, v in self.get_cot_gold().items() if k != "net"},
            "ratio": self.get_gold_silver(),
            "risk": self.get_risk_basket(),
        }

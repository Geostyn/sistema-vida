"""
News Sentiment — capa de riesgo geopolítico/sentimiento para el oro (MODO SOMBRA).

Idea (pedido usuario 2026-06-18): el oro se mueve por guerras, declaraciones de
Trump/Fed, escaladas, etc. No se puede operar el titular al milisegundo (los
algos institucionales llegan antes), pero SÍ se puede usar como CONTEXTO/RIESGO:
detectar régimen risk-off / escalada y, en el futuro, reducir tamaño o pausar.

Diseño honesto:
  - Gratis y sin LLM: lee titulares (Yahoo Finance oro + GDELT geopolítica) y los
    puntúa por keywords (risk-off = alcista oro, risk-on = bajista oro).
  - Cero dependencias nuevas (requests + stdlib). Caché para no machacar.
  - MODO SOMBRA: get_risk_score() devuelve un score; NO toca las señales. Se
    muestra en /radiografia. Validación = forward-live (las noticias no se
    backtestean), igual que la LSTM.

Returns get_risk_score():
  {
    "score": float [-1..+1]  (+ = alcista oro / risk-off),
    "gold_bias": "BULLISH" | "NEUTRAL" | "BEARISH",
    "risk_off_hits": int, "risk_on_hits": int,
    "escalation": bool,      # titulares de guerra/escalada fuerte
    "n_headlines": int, "top": [str, ...], "source": str,
  }
"""

import re
import time
import logging

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# Risk-OFF → alcista para el oro (refugio)
BULLISH_KW = [
    "war", "conflict", "attack", "missile", "strike", "invasion", "invade",
    "sanction", "escalat", "geopolitic", "crisis", "recession", "inflation",
    "dovish", "rate cut", "rate-cut", "safe haven", "safe-haven", "default",
    "tension", "nuclear", "tariff", "shutdown", "stimulus", "uncertainty",
    "weak dollar", "gold rises", "gold surges", "gold hits", "haven",
]
# Risk-ON → bajista para el oro
BEARISH_KW = [
    "ceasefire", "cease-fire", "peace", "truce", "deal reached", "agreement",
    "hawkish", "rate hike", "rate-hike", "strong jobs", "strong economy",
    "risk appetite", "rally", "optimism", "resolution", "stronger dollar",
    "gold falls", "gold drops", "gold slips", "yields rise", "yields jump",
]
# Escalada fuerte (flag de riesgo extremo)
ESCALATION_KW = ["war", "missile", "invasion", "nuclear", "airstrike",
                 "attack", "escalat", "military strike"]

_YAHOO_GOLD = ("https://feeds.finance.yahoo.com/rss/2.0/headline"
               "?s=GC=F&region=US&lang=en-US")
_GDELT = ("https://api.gdeltproject.org/api/v2/doc/doc"
          "?query=(gold%20price%20OR%20geopolitical%20OR%20Federal%20Reserve)"
          "&mode=artlist&format=json&maxrecords=25&timespan=24h&sourcelang=eng")


class NewsSentiment:
    def __init__(self, config: dict | None = None):
        cfg = (config or {}).get("news_sentiment", {})
        self.enabled  = bool(cfg.get("enabled", True))
        self.ttl      = int(cfg.get("cache_min", 15)) * 60
        self._cache   = None
        self._cache_t = 0.0

    # ── Fetch de titulares (con fallback) ──────────────────────────
    def _fetch_yahoo(self) -> list[str]:
        try:
            r = requests.get(_YAHOO_GOLD, timeout=6, verify=False,
                             headers={"User-Agent": "Mozilla/5.0"})
            # RSS: extraer <title>...</title> (saltar el primero = título del feed)
            titles = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                                r.text, re.DOTALL)
            return [t.strip() for t in titles[1:] if t.strip()]
        except Exception as e:
            logger.debug(f"news yahoo error: {e}")
            return []

    def _fetch_gdelt(self) -> list[str]:
        try:
            r = requests.get(_GDELT, timeout=8, verify=False,
                             headers={"User-Agent": "Mozilla/5.0"})
            data = r.json()
            return [a.get("title", "").strip()
                    for a in data.get("articles", []) if a.get("title")]
        except Exception as e:
            logger.debug(f"news gdelt error: {e}")
            return []

    # ── Score ──────────────────────────────────────────────────────
    def get_risk_score(self) -> dict:
        neutral = {"score": 0.0, "gold_bias": "NEUTRAL", "risk_off_hits": 0,
                   "risk_on_hits": 0, "escalation": False, "n_headlines": 0,
                   "top": [], "source": "—"}
        if not self.enabled:
            return neutral

        now = time.time()
        if self._cache is not None and (now - self._cache_t) < self.ttl:
            return self._cache

        headlines = self._fetch_yahoo()
        src = "Yahoo(GC=F)"
        gd = self._fetch_gdelt()
        if gd:
            headlines += gd
            src = "Yahoo+GDELT"
        if not headlines:
            self._cache, self._cache_t = neutral, now
            return neutral

        text_all = " || ".join(headlines).lower()
        off = sum(text_all.count(k) for k in BULLISH_KW)
        on  = sum(text_all.count(k) for k in BEARISH_KW)
        escalation = any(k in text_all for k in ESCALATION_KW)

        total = off + on
        score = 0.0 if total == 0 else round((off - on) / total, 3)
        bias = "BULLISH" if score >= 0.2 else "BEARISH" if score <= -0.2 else "NEUTRAL"

        # Titulares más relevantes (los que disparan keywords)
        flagged = [h for h in headlines
                   if any(k in h.lower() for k in BULLISH_KW + BEARISH_KW)]

        result = {
            "score": score, "gold_bias": bias,
            "risk_off_hits": off, "risk_on_hits": on,
            "escalation": escalation, "n_headlines": len(headlines),
            "top": flagged[:4], "source": src,
        }
        self._cache, self._cache_t = result, now
        return result


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.disable(logging.CRITICAL)
    ns = NewsSentiment({"news_sentiment": {"enabled": True}})
    r = ns.get_risk_score()
    print("Sentimiento de noticias (oro):")
    for k, v in r.items():
        if k != "top":
            print(f"  {k}: {v}")
    print("  titulares marcados:")
    for h in r["top"]:
        print(f"    · {h}")

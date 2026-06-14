"""
News Feed — Calendario económico.

Fuente primaria: Finnhub API.
Fallback automático: ForexFactory (feed JSON gratuito de faireconomy.media)
— necesario desde 2026-06: Finnhub movió /calendar/economic a plan de pago
(403) y el bot se quedó SIN noticias (blackout y alertas inoperativos).
"""

import requests
import logging
import urllib3
from datetime import datetime, timedelta, timezone

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)
SSL_VERIFY = False  # Windows local: desactivado para evitar error de certificados

# Países relevantes para los pares que operamos
RELEVANT_COUNTRIES = {"US", "EU", "GB", "JP"}

# ForexFactory (fallback gratuito) — divisa → código de país interno
FF_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]
FF_COUNTRY_MAP = {"USD": "US", "EUR": "EU", "GBP": "GB", "JPY": "JP"}

# Palabras clave para detectar eventos críticos aunque no estén en Finnhub
HIGH_IMPACT_KEYWORDS = [
    "nonfarm", "nfp", "payroll", "interest rate", "federal funds",
    "fomc", "cpi", "inflation", "gdp", "unemployment",
    "retail sales", "pmi", "ecb rate",
]


class NewsFeed:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://finnhub.io/api/v1"
        self._cache: list = []
        self._cache_time: datetime | None = None
        self._cache_ttl_min = 30  # Renovar cache cada 30 minutos

    # ──────────────────────────────────────────────────────────
    # Descarga y cache
    # ──────────────────────────────────────────────────────────

    def _fetch_calendar(self, days_ahead: int = 3) -> list:
        """
        Descarga el calendario económico. Finnhub primero; si falla
        (403 plan gratuito, red, etc.) → fallback ForexFactory.
        Returns: lista en formato Finnhub (time/country/event/impact/...).
        """
        if self.api_key and self.api_key != "TU_API_KEY_FINNHUB":
            now = datetime.now(timezone.utc)
            params = {
                "from":  now.strftime("%Y-%m-%d"),
                "to":    (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d"),
                "token": self.api_key,
            }
            try:
                resp = requests.get(
                    f"{self.base_url}/calendar/economic",
                    params=params,
                    timeout=10,
                    verify=SSL_VERIFY,
                )
                resp.raise_for_status()
                events = resp.json().get("economicCalendar", [])
                if events:
                    return events
            except requests.exceptions.RequestException as e:
                logger.debug(f"Finnhub calendario no disponible ({e}) — usando ForexFactory")

        return self._fetch_forexfactory()

    def _fetch_forexfactory(self) -> list:
        """
        Fallback GRATUITO: calendario semanal de ForexFactory.
        Mapea al formato Finnhub. Limitación: el feed no publica el dato
        'actual' tras el release (las pre-alertas T-60/T-15 no se ven
        afectadas; el post-release solo se envía si hay 'actual').
        """
        out = []
        for url in FF_URLS:
            try:
                resp = requests.get(
                    url, timeout=10, verify=SSL_VERIFY,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                resp.raise_for_status()
                for ev in resp.json():
                    country = FF_COUNTRY_MAP.get(str(ev.get("country", "")).upper())
                    if not country:
                        continue
                    out.append({
                        "time":     ev.get("date", ""),          # ISO con offset
                        "country":  country,
                        "event":    ev.get("title", ""),
                        "impact":   str(ev.get("impact", "")).lower(),
                        "actual":   ev.get("actual"),
                        "estimate": ev.get("forecast"),
                        "previous": ev.get("previous"),
                    })
            except requests.exceptions.RequestException as e:
                # nextweek.json devuelve 404 hasta el fin de semana — normal
                logger.debug(f"ForexFactory ({url}): {e}")
        if out:
            logger.info(f"Calendario ForexFactory: {len(out)} eventos cargados (fallback)")
        return out

    def _is_cache_valid(self) -> bool:
        if self._cache_time is None:
            return False
        age_min = (datetime.now(timezone.utc) - self._cache_time).total_seconds() / 60
        return age_min < self._cache_ttl_min

    def get_events(self, force_refresh: bool = False) -> list:
        """
        Devuelve lista de eventos económicos.
        Usa cache interno para no exceder rate limits de la API gratuita.
        """
        if not force_refresh and self._is_cache_valid():
            return self._cache

        raw = self._fetch_calendar()
        events = []
        for ev in raw:
            country = ev.get("country", "").upper()
            impact  = ev.get("impact", "").lower()
            time_str = ev.get("time", "")

            try:
                event_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                # Garantizar que siempre sea timezone-aware Y en UTC
                # (ForexFactory entrega offset -04:00; los mensajes muestran UTC)
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=timezone.utc)
                else:
                    event_time = event_time.astimezone(timezone.utc)
            except (ValueError, TypeError):
                continue

            events.append({
                "time":     event_time,
                "country":  country,
                "event":    ev.get("event", ""),
                "impact":   impact,
                "actual":   ev.get("actual"),
                "estimate": ev.get("estimate"),
                "previous": ev.get("previous"),
            })

        self._cache = events
        self._cache_time = datetime.now(timezone.utc)
        return events

    # ──────────────────────────────────────────────────────────
    # Filtros de impacto
    # ──────────────────────────────────────────────────────────

    def get_high_impact_events(self, hours_ahead: int = 24) -> list:
        """Filtra solo eventos de alto impacto de países relevantes en las próximas N horas."""
        now    = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)
        return [
            ev for ev in self.get_events()
            if now <= ev["time"] <= cutoff
            and ev["impact"] == "high"
            and ev["country"] in RELEVANT_COUNTRIES
        ]

    def is_news_blackout(self, minutes_buffer: int = 30) -> dict:
        """
        Verifica si estamos en zona de 'blackout' por noticia próxima.

        Blackout activo si hay un evento de alto impacto en los próximos
        N minutos o dentro de los últimos 5 minutos.

        Returns:
            {
              "blackout": bool,
              "reason":   str,        # descripción del evento
              "next_event": dict|None # info del próximo evento
            }
        """
        now = datetime.now(timezone.utc)
        events = self.get_high_impact_events(hours_ahead=4)

        for ev in sorted(events, key=lambda x: x["time"]):
            diff_min = (ev["time"] - now).total_seconds() / 60

            # Zona de blackout: desde -5 min hasta +N min del evento
            if -5 <= diff_min <= minutes_buffer:
                return {
                    "blackout":   True,
                    "reason":     f"{ev['event']} ({ev['country']}) en {int(diff_min)}min",
                    "next_event": ev,
                }

        # Sin blackout — devuelve el próximo evento para información
        upcoming = sorted(events, key=lambda x: x["time"])
        return {
            "blackout":   False,
            "reason":     "",
            "next_event": upcoming[0] if upcoming else None,
        }

    def get_daily_summary(self) -> list:
        """Todos los eventos de alto impacto de hoy, ordenados por hora."""
        now           = datetime.now(timezone.utc)
        start_of_day  = now.replace(hour=0,  minute=0,  second=0,  microsecond=0)
        end_of_day    = now.replace(hour=23, minute=59, second=59, microsecond=0)

        return sorted(
            [
                ev for ev in self.get_events()
                if start_of_day <= ev["time"] <= end_of_day
                and ev["impact"] == "high"
            ],
            key=lambda x: x["time"],
        )

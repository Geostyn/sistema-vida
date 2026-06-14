"""
News Alert Manager — Alertas PROACTIVAS de noticias económicas por Telegram.

Implementa lo declarado en config["news_alerts"] (antes solo existía en yaml):
  - T-60 y T-15: aviso ANTES de cada dato de alto impacto que afecta al oro
    (NFP, FOMC, CPI, GDP...), con consenso/previo y lectura esperada.
  - Post-release: actual vs esperado y lectura de impacto para XAUUSD.
  - Regla FundedNext: recordatorio de no abrir/cerrar ±5 min del dato.

El usuario es trader: las alertas le dan contexto, no solo el titular.
Anti-spam: cada (evento, etapa) se alerta UNA sola vez (estado en memoria).
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Eventos cuyo dato "mejor de lo esperado" es USD-NEGATIVO (inversos):
# más desempleo / más peticiones de paro = USD débil = oro alcista
INVERSE_EVENTS = ("unemployment", "jobless", "claims")


def _is_usd_inverse(event_name: str) -> bool:
    name = (event_name or "").lower()
    return any(k in name for k in INVERSE_EVENTS)


def _gold_read(event_name: str, actual, estimate) -> str:
    """Lectura del dato para XAUUSD (heurística direccional)."""
    try:
        a, e = float(actual), float(estimate)
    except (TypeError, ValueError):
        return ""
    if a == e:
        return "📊 En línea con lo esperado → impacto limitado en el oro"
    stronger = a > e  # dato mayor que consenso
    usd_up = stronger != _is_usd_inverse(event_name)
    if usd_up:
        return ("📉 Dato fuerte para el USD → presión BAJISTA para el oro "
                "(vigila el DXY)")
    return ("📈 Dato débil para el USD → viento a favor ALCISTA para el oro "
            "(vigila el DXY)")


class NewsAlertManager:
    def __init__(self, news_feed, telegram, config: dict):
        self.news     = news_feed
        self.telegram = telegram
        cfg           = config.get("news_alerts", {}) or {}
        self.enabled  = bool(cfg.get("enabled", False))
        self.pre_minutes  = [int(m) for m in cfg.get("pre_alert_minutes", [60, 15])]
        self.post_release = bool(cfg.get("post_release", True))
        self.funded_win   = int(cfg.get("funded_window_min", 5))
        # (event_key, etapa) ya alertados — etapa = "pre60" | "pre15" | "post"
        self._alerted: set = set()
        self._last_post_refresh = None  # rate limit del force_refresh

    @staticmethod
    def _event_key(ev: dict) -> str:
        t = ev["time"].isoformat() if hasattr(ev["time"], "isoformat") else str(ev["time"])
        return f"{ev.get('country','')}|{ev.get('event','')}|{t}"

    def check(self) -> int:
        """
        Llamar cada ciclo (~60s). Revisa eventos HIGH próximos/pasados y
        envía las alertas pendientes. Returns: nº de alertas enviadas.
        """
        if not self.enabled or not self.telegram or not self.telegram.enabled:
            return 0
        sent = 0
        now  = datetime.now(timezone.utc)

        try:
            events = self.news.get_high_impact_events(hours_ahead=3)
        except Exception as e:
            logger.debug(f"NewsAlerts get events: {e}")
            return 0

        # ── Pre-alertas T-60 / T-15 ────────────────────────────────
        for ev in events:
            mins_to = (ev["time"] - now).total_seconds() / 60
            key     = self._event_key(ev)
            for pre in sorted(self.pre_minutes, reverse=True):
                stage = f"pre{pre}"
                # Ventana de disparo: [pre-3, pre] min antes del evento
                if (key, stage) in self._alerted or not (pre - 3 <= mins_to <= pre):
                    continue
                self._alerted.add((key, stage))
                if self._send_pre_alert(ev, mins_to):
                    sent += 1

        # ── Post-release: actual vs esperado ──────────────────────
        if self.post_release:
            sent += self._check_post_release(now)

        # Limpiar claves viejas (> 1 día) para no crecer sin límite
        if len(self._alerted) > 300:
            self._alerted = set(list(self._alerted)[-150:])
        return sent

    def _send_pre_alert(self, ev: dict, mins_to: float) -> bool:
        t_local = ev["time"].strftime("%H:%M UTC")
        est     = ev.get("estimate")
        prev    = ev.get("previous")
        est_str  = f"\n📊 Consenso: <b>{est}</b>"  if est  is not None else ""
        prev_str = f"  |  Previo: {prev}"          if prev is not None else ""
        urgent   = "🚨" if mins_to <= 20 else "📅"

        # Contexto direccional anticipado (el usuario es trader)
        inverse = _is_usd_inverse(ev.get("event", ""))
        if inverse:
            read = ("Si sale MAYOR de lo esperado → USD débil → oro ALCISTA.\n"
                    "Si sale MENOR → USD fuerte → oro BAJISTA.")
        else:
            read = ("Si sale MAYOR de lo esperado → USD fuerte → oro BAJISTA.\n"
                    "Si sale MENOR → USD débil → oro ALCISTA.")

        msg = (
            f"{urgent} <b>NOTICIA DE ALTO IMPACTO en {mins_to:.0f} min</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📰 <b>{ev.get('event','')}</b> ({ev.get('country','')})\n"
            f"🕐 {t_local}{est_str}{prev_str}\n\n"
            f"💡 <b>Lectura para XAUUSD:</b>\n{read}\n\n"
            f"🏦 <b>FundedNext:</b> NO abrir/cerrar en ±{self.funded_win} min "
            f"del dato (beneficio solo cuenta 40%).\n"
            f"⚠️ Volatilidad y spread se disparan — protege posiciones abiertas "
            f"(SL a BE o cierra parcial)."
        )
        return self.telegram.send_message(msg)

    def _check_post_release(self, now) -> int:
        """Busca eventos publicados hace 2-20 min y manda actual vs esperado."""
        sent = 0
        # Solo refrescar la API si el CACHE indica un dato recién publicado
        # pendiente de alertar (protege el rate limit de Finnhub)
        try:
            cached = self.news.get_events()
        except Exception:
            return 0
        pending = [
            ev for ev in cached
            if ev.get("impact") == "high"
            and 2 <= (now - ev["time"]).total_seconds() / 60 <= 20
            and (self._event_key(ev), "post") not in self._alerted
        ]
        if not pending:
            return 0
        # Máx 1 refresh cada 3 min mientras esperamos el "actual"
        if self._last_post_refresh and \
                (now - self._last_post_refresh).total_seconds() < 180:
            all_events = cached
        else:
            self._last_post_refresh = now
            try:
                all_events = self.news.get_events(force_refresh=True)
            except Exception:
                all_events = cached
        for ev in all_events:
            if ev.get("impact") != "high":
                continue
            mins_since = (now - ev["time"]).total_seconds() / 60
            if not (2 <= mins_since <= 20):
                continue
            key = self._event_key(ev)
            if (key, "post") in self._alerted:
                continue
            actual = ev.get("actual")
            if actual is None:
                continue  # Finnhub aún no publicó el actual — reintentar próximo ciclo
            self._alerted.add((key, "post"))

            est  = ev.get("estimate")
            read = _gold_read(ev.get("event", ""), actual, est)
            est_str = f"  |  Consenso: {est}" if est is not None else ""
            msg = (
                f"📰 <b>DATO PUBLICADO</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>{ev.get('event','')}</b> ({ev.get('country','')})\n"
                f"🔢 Actual: <b>{actual}</b>{est_str}\n"
                f"{read}\n\n"
                f"⏳ Espera a que el spread se normalice (~15 min) antes de "
                f"ejecutar señales nuevas."
            )
            if self.telegram.send_message(msg):
                sent += 1
        return sent

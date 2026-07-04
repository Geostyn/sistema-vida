"""
Reasoning Engine — Genera explicaciones en español de por qué se emite cada señal.

Sin LLM (cero coste, cero latencia) — plantillas dinámicas basadas en el estado
de cada confluencia. Resultado incluido en el campo 'reasoning' de la señal
y mostrado en el mensaje de Telegram.
"""

from datetime import datetime, timezone


def _session_name(hour_utc: int) -> str:
    if 7 <= hour_utc < 12:
        return "sesión de Londres"
    if 12 <= hour_utc < 17:
        return "overlap Londres-NY (máxima liquidez)"
    if 17 <= hour_utc < 21:
        return "sesión de Nueva York"
    return "sesión asiática (baja liquidez)"


def generate_reasoning(signal: dict, macro_data: dict, regime_data: dict) -> str:
    """
    Genera un bloque de texto explicando POR QUÉ se emite la señal.
    Máximo 5-6 líneas concisas.
    """
    direction  = signal.get("direction", "BUY")
    bias_h4    = signal.get("bias_h4", "BULLISH")
    rsi_val    = signal.get("rsi_value") or 50.0
    confluences = signal.get("confluences", 0)
    rr         = signal.get("rr", 2.0)
    vp_in      = any("VP" in d or "Value" in d or "POC" in d for d in signal.get("confluence_details", []))
    delta_in   = any("Delta" in d or "delta" in d for d in signal.get("confluence_details", []))

    dxy_data    = macro_data.get("details", {}).get("dxy", {})
    bonds_data  = macro_data.get("details", {}).get("bonds_10y", {})

    regime      = regime_data.get("regime", "UNKNOWN")
    adx         = regime_data.get("adx", 20.0)
    hurst       = regime_data.get("hurst", 0.5)

    hour_utc    = datetime.now(timezone.utc).hour
    session     = _session_name(hour_utc)

    lines = []

    # ── Línea 1: Estructura principal ────────────────────────────
    ob_es  = "soporte" if direction == "BUY" else "resistencia"
    lines.append(
        f"📍 <b>Estructura H4 {bias_h4}</b> con Order Block como {ob_es} "
        f"institucional — {confluences}/16.5 confluencias."
    )

    # ── Línea 2: RSI / momentum ───────────────────────────────────
    if rsi_val < 35:
        lines.append(
            f"📉 RSI {rsi_val:.0f} en sobreventa → zona donde "
            f"los institucionales suelen acumular posiciones largas."
        )
    elif rsi_val > 65:
        lines.append(
            f"📈 RSI {rsi_val:.0f} en sobrecompra → zona donde "
            f"los institucionales suelen distribuir y vender."
        )
    else:
        lines.append(
            f"📊 RSI {rsi_val:.0f} en zona neutral — el impulso no está agotado, "
            f"margen para recorrer hasta TP."
        )

    # ── Línea 3: DXY ─────────────────────────────────────────────
    dxy_val = dxy_data.get("valor")
    dxy_chg = float(dxy_data.get("cambio_6h", 0))
    if dxy_val:
        arrow  = "↓" if dxy_chg < 0 else "↑"
        if dxy_chg < -0.08 and direction == "BUY":
            lines.append(
                f"💵 DXY {dxy_val:.2f} ({arrow}{abs(dxy_chg):.2f}% en 6h) — "
                f"dólar debilitándose → impulso adicional para el oro."
            )
        elif dxy_chg > 0.08 and direction == "SELL":
            lines.append(
                f"💵 DXY {dxy_val:.2f} ({arrow}{abs(dxy_chg):.2f}% en 6h) — "
                f"dólar fortaleciéndose → presión bajista sobre el oro."
            )
        else:
            lines.append(
                f"💵 DXY {dxy_val:.2f} ({arrow}{abs(dxy_chg):.2f}% en 6h) — "
                f"movimiento neutro del dólar."
            )

    # ── Línea 4: Bonds / Yields ───────────────────────────────────
    y_val = bonds_data.get("valor")
    y_chg = float(bonds_data.get("cambio_6h", 0))
    if y_val:
        if y_chg < -0.04 and direction == "BUY":
            lines.append(
                f"📉 Yields 10Y {y_val:.3f}% cayendo → coste de oportunidad "
                f"del oro baja → flujos hacia el metal."
            )
        elif y_chg > 0.04 and direction == "SELL":
            lines.append(
                f"📈 Yields 10Y {y_val:.3f}% subiendo → inversores "
                f"prefieren bonos sobre oro → presión bajista."
            )

    # ── Línea 5: Régimen de mercado ───────────────────────────────
    if regime in ("TRENDING_UP", "TRENDING_DOWN"):
        mem = "alta memoria de tendencia" if hurst > 0.58 else "tendencia moderada"
        lines.append(
            f"📊 Régimen {regime} — ADX {adx:.0f} ({mem}, Hurst {hurst:.2f}) → "
            f"mercado con dirección clara, mayor probabilidad de continuación."
        )
    elif regime == "RANGING":
        lines.append(
            f"⚠️ Régimen RANGING — ADX {adx:.0f} — mercado sin dirección clara; "
            f"confluencias elevadas requeridas (umbar subido automáticamente)."
        )

    # ── Línea 6: VP / Delta institucional ────────────────────────
    if vp_in and delta_in:
        lines.append(
            f"🏦 Volume Profile (futuros COMEX) + Delta de ticks confirman "
            f"presencia institucional {'comprando' if direction == 'BUY' else 'vendiendo'} en esta zona."
        )
    elif vp_in:
        lines.append(
            f"🏦 Precio en zona de valor COMEX (POC/VAH/VAL) — "
            f"nivel relevante para traders institucionales."
        )
    elif delta_in:
        lines.append(
            f"📦 Delta de ticks muestra desequilibrio "
            f"{'comprador' if direction == 'BUY' else 'vendedor'} en tiempo real."
        )

    # ── Línea 7: Sesión ──────────────────────────────────────────
    if "overlap" in session or "York" in session:
        lines.append(f"⏰ {session.capitalize()} — sesión con mayor volumen y spreads más ajustados.")

    # ── R:R ──────────────────────────────────────────────────────
    lines.append(f"🎯 Risk/Reward 1:{rr:.1f} — riesgo controlado con SL en extremo del OB + ATR buffer.")

    return "\n".join(lines)


def generate_news_context(news_calendar: list) -> str:
    """
    Genera el bloque de noticias económicas del día para incluir en Telegram.
    Filtra solo eventos HIGH y MEDIUM de las próximas 8 horas.
    """
    if not news_calendar:
        return ""

    now     = datetime.now(timezone.utc)
    lines   = []
    shown   = 0

    for ev in news_calendar:
        if shown >= 4:
            break
        ev_time  = ev.get("time")
        impact   = ev.get("impact", "LOW")
        ev_name  = ev.get("event", "Evento")

        if impact not in ("HIGH", "MEDIUM"):
            continue

        # Formatear hora
        try:
            if hasattr(ev_time, "strftime"):
                time_str = ev_time.strftime("%H:%M")
                hours_away = (ev_time.replace(tzinfo=timezone.utc) - now).total_seconds() / 3600
            else:
                time_str   = str(ev_time)[:5]
                hours_away = 0
        except Exception:
            time_str   = "?:??"
            hours_away = 0

        impact_emoji = "🔴" if impact == "HIGH" else "🟡"
        note         = " ← próximo!" if 0 < hours_away < 2 else (" ← CUIDADO" if -0.5 < hours_away <= 0 else "")
        lines.append(f"  {impact_emoji} {time_str} UTC — {ev_name}{note}")
        shown += 1

    if not lines:
        return ""

    return "📰 <b>Eventos macro hoy:</b>\n" + "\n".join(lines)

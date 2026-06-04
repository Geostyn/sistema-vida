"""
Telegram Bot — Envía alertas de trading via Telegram Bot API.
Usa requests directamente (sin librería async) para máxima simplicidad.

Cómo configurar:
  1. Abre Telegram y busca @BotFather
  2. Escribe /newbot → sigue las instrucciones → copia el token
  3. Busca @userinfobot → escribe /start → copia tu Id (número)
  4. Pega token y chat_id en config.yaml
"""

import requests
import logging
import urllib3
from datetime import datetime, timezone

# Suprimir advertencia de SSL en Windows (uso local, no es un servidor)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
SSL_VERIFY = False  # Windows local: desactivado para evitar error de certificados


class TelegramBot:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token       = bot_token
        self.chat_id         = str(chat_id)
        self._last_update_id = 0  # offset para polling de getUpdates
        self.enabled   = (
            bool(bot_token)
            and bot_token  != "TU_BOT_TOKEN"
            and bool(chat_id)
            and chat_id != "TU_CHAT_ID"
        )
        if not self.enabled:
            logger.info("Telegram no configurado — edita config.yaml para activarlo")

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Envía un mensaje de texto al chat configurado."""
        if not self.enabled:
            return False

        url  = TELEGRAM_API.format(token=self.bot_token, method="sendMessage")
        data = {"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode}

        try:
            resp = requests.post(url, json=data, timeout=30, verify=SSL_VERIFY)
            resp.raise_for_status()
            return True
        except requests.exceptions.Timeout:
            logger.error("Telegram: timeout de conexion. Verifica tu red o VPN.")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Error enviando Telegram: {e}")
            return False

    def send_signal(self, signal: dict) -> bool:
        """Formatea y envía una señal completa."""
        msg = self._format_signal(signal)
        ok  = self.send_message(msg)
        if ok:
            logger.info(f"Señal enviada a Telegram: {signal.get('symbol')} {signal.get('direction')}")
        return ok

    def send_active_message(self, balance: float = 0, currency: str = "EUR") -> bool:
        """Mensaje de SISTEMA ACTIVO al iniciar."""
        now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
        msg = (
            f"✅ <b>SISTEMA ACTIVO</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {now}\n"
            f"💰 Balance: {balance:,.0f} {currency}\n\n"
            f"Monitoreando:\n"
            f"  📊 XAUUSD · EURUSD · GBPUSD · USDJPY\n\n"
            f"Te avisaré cuando detecte un setup de alta probabilidad."
        )
        return self.send_message(msg)

    def send_inactive_message(self) -> bool:
        """Mensaje de SISTEMA INACTIVO al apagar."""
        now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
        msg = (
            f"🔴 <b>SISTEMA INACTIVO</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {now}\n\n"
            f"El sistema de trading ha sido apagado.\n"
            f"No recibirás más alertas hasta que lo vuelvas a iniciar."
        )
        return self.send_message(msg)

    def send_test_message(self) -> bool:
        """Alias de compatibilidad — usa send_active_message."""
        return self.send_active_message()

    def send_status_update(self, stats: dict) -> bool:
        """Resumen diario de actividad (para llamar 1 vez al día)."""
        signals = stats.get("signals_today", 0)
        pnl     = stats.get("daily_pnl", 0.0)
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

        msg = (
            f"📊 <b>Resumen del día</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📡 Señales generadas: {signals}\n"
            f"💰 P&L registrado:    {pnl_str}\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%d/%m %H:%M UTC')}"
        )
        return self.send_message(msg)

    def send_weekly_report(self, stats: dict) -> bool:
        """Reporte semanal de rendimiento — enviar cada domingo."""
        wins    = stats.get("wins", 0)
        losses  = stats.get("losses", 0)
        pending = stats.get("pending", 0)
        total   = wins + losses
        wr      = f"{wins/total:.1%}" if total > 0 else "—"
        pf      = stats.get("profit_factor", 0)
        pnl     = stats.get("weekly_pnl", 0)
        pnl_str = f"+€{pnl:,.0f}" if pnl >= 0 else f"-€{abs(pnl):,.0f}"
        balance = stats.get("balance", 0)
        bt_wr   = stats.get("backtest_wr", 0.421)

        diff_wr = ""
        if total >= 5:
            real_wr = wins / total
            delta   = real_wr - bt_wr
            diff_wr = f"  {'🔺' if delta >= 0 else '🔻'} vs backtest: {delta:+.1%}"

        msg = (
            f"📅 <b>REPORTE SEMANAL</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%d/%m/%Y')}\n\n"
            f"📊 <b>Señales cerradas:</b> {total}\n"
            f"  ✅ WIN:  {wins}\n"
            f"  ❌ LOSS: {losses}\n"
            f"  ⏳ Pendientes: {pending}\n\n"
            f"📈 <b>Win Rate:</b> {wr}{diff_wr}\n"
            f"💹 <b>Profit Factor:</b> {pf:.2f}\n"
            f"💰 <b>P&L semana:</b> {pnl_str}\n"
            f"🏦 <b>Balance actual:</b> €{balance:,.0f}\n\n"
            f"⚠️ No es consejo financiero."
        )
        return self.send_message(msg)

    # ──────────────────────────────────────────────────────────
    # POLLING BIDIRECCIONAL — leer comandos del usuario
    # ──────────────────────────────────────────────────────────

    def get_pending_commands(self) -> list:
        """
        Consulta Telegram por nuevos mensajes del usuario (non-blocking).
        Detecta: /cancelar <ticket>, /modificar <ticket> SL:<p> TP:<p>, /cerrar <ticket>

        Returns:
            Lista de dicts: [{type, ticket, sl, tp}, ...]
        """
        if not self.enabled:
            return []

        url    = TELEGRAM_API.format(token=self.bot_token, method="getUpdates")
        params = {
            "offset":          self._last_update_id + 1,
            "timeout":         0,
            "allowed_updates": ["message"],
        }
        try:
            resp = requests.get(url, params=params, timeout=5, verify=SSL_VERIFY)
            data = resp.json()
        except Exception:
            return []

        commands = []
        for update in data.get("result", []):
            self._last_update_id = max(self._last_update_id, update["update_id"])
            msg  = update.get("message", {})
            text = msg.get("text", "").strip().lower()
            if not text.startswith("/"):
                continue

            parts = text.split()
            cmd   = parts[0]

            if cmd == "/cancelar" and len(parts) >= 2:
                try:
                    commands.append({"type": "cancelar", "ticket": int(parts[1])})
                except ValueError:
                    pass

            elif cmd == "/cerrar" and len(parts) >= 2:
                try:
                    commands.append({"type": "cerrar", "ticket": int(parts[1])})
                except ValueError:
                    pass

            elif cmd == "/modificar" and len(parts) >= 2:
                cmd_dict = {"type": "modificar", "ticket": None, "sl": None, "tp": None}
                try:
                    cmd_dict["ticket"] = int(parts[1])
                except ValueError:
                    continue
                for part in parts[2:]:
                    if part.upper().startswith("SL:"):
                        try: cmd_dict["sl"] = float(part.split(":")[1])
                        except ValueError: pass
                    elif part.upper().startswith("TP:"):
                        try: cmd_dict["tp"] = float(part.split(":")[1])
                        except ValueError: pass
                if cmd_dict["ticket"]:
                    commands.append(cmd_dict)

            elif cmd == "/status":
                commands.append({"type": "status"})

            elif cmd == "/revisar":
                commands.append({"type": "revisar"})

        return commands

    # ──────────────────────────────────────────────────────────
    # Notificaciones de órdenes MT5
    # ──────────────────────────────────────────────────────────

    def send_order_placed(self, signal: dict, ticket: int) -> bool:
        """Notifica que se colocó una orden LIMIT en MT5."""
        symbol    = signal.get("symbol", "")
        direction = signal.get("direction", "")
        entry     = signal.get("entry", 0)
        sl        = signal.get("sl", 0)
        tp1       = signal.get("tp1", 0)
        rr        = signal.get("rr", 0)
        conf      = signal.get("confidence", 0)
        lots      = signal.get("lot_size", "?")
        dir_emoji = "🟢" if direction == "BUY" else "🔴"

        msg = (
            f"🔔 <b>ORDEN COLOCADA EN MT5</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{dir_emoji} <b>{symbol} {direction}</b>\n\n"
            f"📍 <b>Entry:</b>   <code>{entry:.5f}</code>\n"
            f"🛑 <b>SL:</b>      <code>{sl:.5f}</code>\n"
            f"🎯 <b>TP:</b>      <code>{tp1:.5f}</code>\n"
            f"📊 R:R 1:{rr:.1f} | Conf {conf:.0%}\n"
            f"📦 Lotes: {lots:.2f} | Ticket #{ticket}\n\n"
            f"✅ La orden ejecutará sola cuando el precio llegue a <code>{entry:.5f}</code>\n\n"
            f"<b>Comandos:</b>\n"
            f"🚫 Cancelar: <code>/cancelar {ticket}</code>\n"
            f"✏️ Modificar: <code>/modificar {ticket} SL:{sl:.5f} TP:{tp1:.5f}</code>"
        )
        return self.send_message(msg)

    def send_order_executed(self, symbol: str, direction: str,
                             entry: float, ticket: int) -> bool:
        """Notifica que MT5 ejecutó la orden (precio alcanzó el entry)."""
        dir_emoji = "🟢" if direction == "BUY" else "🔴"
        msg = (
            f"✅ <b>ORDEN EJECUTADA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{dir_emoji} <b>{symbol} {direction}</b>\n"
            f"📍 Entry: <code>{entry:.5f}</code>\n"
            f"🎫 Ticket: #{ticket}\n\n"
            f"El trade está activo. SL/TP ya están configurados en MT5.\n"
            f"Para cerrar: <code>/cerrar {ticket}</code>"
        )
        return self.send_message(msg)

    def send_order_expired(self, symbol: str, direction: str,
                            entry: float, ticket: int) -> bool:
        """Notifica que una orden LIMIT expiró sin ejecutarse."""
        msg = (
            f"⏰ <b>ORDEN EXPIRADA</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {symbol} {direction} @ <code>{entry:.5f}</code>\n"
            f"Ticket #{ticket}\n\n"
            f"El precio no llegó al entry en el tiempo configurado. Orden cancelada."
        )
        return self.send_message(msg)

    def send_breakeven_alert(self, symbol: str, direction: str,
                              entry: float, current: float) -> bool:
        """Alerta para mover SL a breakeven cuando el trade llega a +1R."""
        msg = (
            f"🔔 <b>MOVER SL A BREAKEVEN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {symbol} {direction}\n"
            f"💰 Precio actual: <code>{current:.5f}</code>\n"
            f"📍 Mover SL a entrada: <code>{entry:.5f}</code>\n"
            f"✅ Trade en +1R — riesgo en 0"
        )
        return self.send_message(msg)

    # ──────────────────────────────────────────────────────────
    # Formateo de señales
    # ──────────────────────────────────────────────────────────

    def _format_signal(self, signal: dict) -> str:
        direction    = signal.get("direction", "")
        symbol       = signal.get("symbol", "")
        entry        = signal.get("entry", 0)
        sl           = signal.get("sl", 0)
        tp1          = signal.get("tp1", 0)
        tp2          = signal.get("tp2")
        confidence   = signal.get("confidence", 0)
        confluences  = signal.get("confluences", 0)
        cf_details   = signal.get("confluence_details", [])
        news_warn    = signal.get("news_warning", "")
        news_context = signal.get("news_context", "")
        bias_h4      = signal.get("bias_h4", "")
        rsi_state    = signal.get("rsi_state", "")
        rsi_val      = signal.get("rsi_value")
        lot_size     = signal.get("lot_size")
        timestamp    = signal.get("timestamp", "")
        reasoning    = signal.get("reasoning", "")
        regime       = signal.get("regime", "")
        ml_proba     = signal.get("ml_proba", 0)

        dir_emoji = "🟢" if direction == "BUY" else "🔴"
        dir_es    = "COMPRA (Alcista)" if direction == "BUY" else "VENTA (Bajista)"

        try:
            dt       = datetime.fromisoformat(timestamp)
            time_str = dt.strftime("%d/%m %H:%M UTC")
        except Exception:
            time_str = ""

        sl_dist  = abs(entry - sl)
        tp1_dist = abs(tp1 - entry)
        tp1_rr   = tp1_dist / sl_dist if sl_dist else 0

        lines = [
            f"📊 <b>SETUP {symbol} | H1 | {time_str}</b>",
            f"━━━━━━━━━━━━━━━━━━━━━━",
            f"{dir_emoji} <b>{dir_es}</b>",
            f"",
            f"📍 <b>Entrada:</b>   <code>{entry:.5f}</code>",
            f"🛑 <b>Stop Loss:</b> <code>{sl:.5f}</code>  ({sl_dist:.5f})",
            f"🎯 <b>TP1:</b>       <code>{tp1:.5f}</code>  — R:R 1:{tp1_rr:.1f}",
        ]

        if tp2 is not None:
            tp2_dist = abs(tp2 - entry)
            tp2_rr   = tp2_dist / sl_dist if sl_dist else 0
            lines.append(f"🎯 <b>TP2:</b>       <code>{tp2:.5f}</code>  — R:R 1:{tp2_rr:.1f}")

        if lot_size is not None:
            lines.append(f"📦 <b>Lotes sugeridos:</b> {lot_size:.2f}")

        regime_str = f" | {regime}" if regime and regime not in ("UNKNOWN", "") else ""
        ml_str     = f" | ML {ml_proba:.0%}" if ml_proba and ml_proba != 0.5 else ""
        lines += [
            f"",
            f"⚡ <b>Confianza:</b> {confidence:.0%}  ({confluences}/14.5{regime_str}{ml_str})",
            f"📈 <b>Bias H4:</b>   {bias_h4}",
        ]

        if rsi_val is not None:
            lines.append(f"📉 <b>RSI:</b>       {rsi_val:.1f}  ({rsi_state})")

        if cf_details:
            lines += [f"", f"✅ <b>Confluencias:</b>"]
            for d in cf_details:
                lines.append(f"  • {d}")

        # ── Razonamiento (nuevo) ──────────────────────────────────
        if reasoning:
            lines += [f"", f"💡 <b>Por qué esta operación:</b>", reasoning]

        # ── Contexto de noticias (nuevo) ─────────────────────────
        if news_context:
            lines += [f"", news_context]

        if news_warn:
            lines += [f"", f"⚠️ <b>Precaución:</b> {news_warn}"]

        obs_note = signal.get("observation_note", "")
        if obs_note:
            lines += [f"", f"🔍 <b>MODO OBSERVACIÓN:</b> {obs_note}", f"<i>Esta señal NO se ha ejecutado en MT5</i>"]

        lines += [f"", f"─────────────────────────", f"⚠️ No es consejo financiero. Gestiona tu riesgo."]

        return "\n".join(lines)

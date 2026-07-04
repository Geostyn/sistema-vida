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

        # Telegram limita a 4096 chars — recortar por el medio (detalle de
        # confluencias/razonamiento), nunca los niveles ni el bloque 2K
        if len(text) > 4000:
            head = text[:2400]
            tail = text[-1400:]
            text = head + "\n  … <i>(detalle recortado)</i> …\n" + tail

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
            f"Monitoreando XAUUSD — 2 streams de señales:\n"
            f"  ⚡ DAYTRADE M15 (intradía, manual — ilimitadas)\n"
            f"  📊 INTRADAY H1 (auto-ejecutada en demo para aprender)\n\n"
            f"Cada señal lleva el bloque 💼 MI CUENTA\n"
            f"con lotes y riesgo calculados para tu cuenta.\n\n"
            f"<b>Comandos:</b>\n"
            f"  /estado — por qué no hay señal ahora\n"
            f"  /radiografia — qué dice cada módulo del cerebro ahora\n"
            f"  /salud — estado de las fuentes de datos\n"
            f"  /status — métricas del sistema\n"
            f"  /micuenta — estado cuenta personal\n"
            f"  /saldo 250.00 — sincronizar saldo personal\n"
            f"  /lote 120 4195 4210 — lote máx por colchón fondeada\n"
            f"  /revisar — órdenes pendientes"
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

            elif cmd == "/estado":
                # Diagnóstico: por qué no hay señal ahora + estado 2K
                commands.append({"type": "estado"})

            elif cmd == "/revisar":
                commands.append({"type": "revisar"})

            elif cmd == "/radiografia":
                # Radiografía en vivo: qué dice cada módulo del cerebro ahora
                commands.append({"type": "radiografia"})

            elif cmd == "/salud":
                # Salud de las fuentes de datos (MT5, yfinance, FRED, COT, noticias…)
                commands.append({"type": "salud"})

            elif cmd == "/funded":
                commands.append({"type": "funded"})

            elif cmd == "/micuenta":
                commands.append({"type": "micuenta"})

            elif cmd == "/saldo" and len(parts) >= 2:
                # /saldo 250.00 — sincronizar saldo real de la cuenta personal
                try:
                    commands.append({
                        "type":   "saldo",
                        "amount": float(parts[1].replace(",", ".")),
                    })
                except ValueError:
                    pass

            elif cmd == "/equity" and len(parts) >= 2:
                # /equity 1985.50 — sincronizar equity real de FundedNext
                try:
                    commands.append({
                        "type":   "equity",
                        "amount": float(parts[1].replace(",", ".")),
                    })
                except ValueError:
                    pass

            elif cmd == "/lote" and len(parts) >= 4:
                # /lote <room> <entry> <sl> [symbol] — lote máx según colchón de
                # drawdown de la cuenta fondeada (dimensiona contra el room, no el balance)
                try:
                    commands.append({
                        "type":   "lote",
                        "room":   float(parts[1].replace(",", ".")),
                        "entry":  float(parts[2].replace(",", ".")),
                        "sl":     float(parts[3].replace(",", ".")),
                        "symbol": parts[4].upper() if len(parts) >= 5 else "XAUUSD",
                    })
                except ValueError:
                    pass

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

    def send_funded_status(self, state: dict) -> bool:
        """Responde /funded (y confirma /equity) con el estado de la cuenta 2K."""
        if not state or not state.get("enabled"):
            return self.send_message("🏦 Cuenta FundedNext desactivada en config.yaml")
        breach = "\n🚨 <b>CUENTA EN BREACH — NO OPERAR</b>" if state.get("breached") else ""
        msg = (
            f"🏦 <b>FUNDEDNEXT 2K — Estado</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Equity: <b>${state['equity']:,.2f}</b>\n"
            f"📈 Máximo: ${state['highest']:,.2f}\n"
            f"🛑 Floor (trailing 6%): <b>${state['floor']:,.2f}</b>\n"
            f"🟢 Room disponible: <b>${state['room']:,.2f}</b>\n"
            f"📉 DD usado: {state['dd_used_pct']:.1f}%{breach}\n\n"
            f"Sincronizar con la cuenta real:\n"
            f"<code>/equity {state['equity']:.2f}</code>"
        )
        return self.send_message(msg)

    def send_funded_result(self, result: dict, state: dict) -> bool:
        """Notifica el P&L simulado 2K de una señal resuelta en la demo."""
        pnl   = result.get("funded_pnl", 0)
        emoji = "✅" if pnl >= 0 else "❌"
        msg = (
            f"🏦 <b>FundedNext 2K — resultado señal #{result['signal_id']}</b>\n"
            f"{emoji} {result['outcome']}: <b>{pnl:+.2f} USD</b> "
            f"(R {result.get('r_realized', 0):+.2f})\n"
            f"💰 Equity simulado: <b>${result['equity']:,.2f}</b> | "
            f"Floor ${state.get('floor', 0):,.2f} | Room ${state.get('room', 0):,.2f}\n"
            f"<i>Si lo ejecutaste distinto, sincroniza: /equity importe</i>"
        )
        return self.send_message(msg)

    def send_personal_status(self, state: dict) -> bool:
        """Responde /micuenta (y confirma /saldo) con el estado de la cuenta personal."""
        if not state or not state.get("enabled"):
            return self.send_message("💼 Cuenta personal desactivada en config.yaml")
        cur   = state.get("currency", "EUR")
        title = state.get("title", "MI CUENTA")
        low   = "\n⚠️ <b>EQUITY BAJO MÍNIMO — señales NO aptas</b>" if state.get("too_low") else ""
        msg = (
            f"💼 <b>{title} — Estado</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Equity: <b>{state['equity']:,.2f} {cur}</b>\n"
            f"📊 P&L acumulado: <b>{state['pnl']:+,.2f} {cur}</b>\n"
            f"🏁 Balance inicial: {state['initial']:,.2f} {cur}{low}\n\n"
            f"Sincronizar con tu cuenta real:\n"
            f"<code>/saldo {state['equity']:.2f}</code>"
        )
        return self.send_message(msg)

    def send_personal_result(self, result: dict, state: dict) -> bool:
        """Notifica el P&L simulado de MI CUENTA al resolverse una señal apta."""
        pnl   = result.get("personal_pnl", 0)
        cur   = state.get("currency", "EUR")
        title = state.get("title", "MI CUENTA")
        emoji = "✅" if pnl >= 0 else "❌"
        msg = (
            f"💼 <b>{title} — resultado señal #{result['signal_id']}</b>\n"
            f"{emoji} {result['outcome']}: <b>{pnl:+.2f} {cur}</b> "
            f"(R {result.get('r_realized', 0):+.2f})\n"
            f"💰 Equity simulado: <b>{result['equity']:,.2f} {cur}</b>\n"
            f"<i>Si lo ejecutaste distinto, sincroniza: /saldo importe</i>"
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
        neural_proba = signal.get("neural_proba")
        inter        = signal.get("intermarket") or {}
        inter_score  = signal.get("inter_score", 0) or 0

        # ── Nivel de convicción A/B/C (combina confianza + macro + ML/NN) ──
        tier_score = float(confidence or 0)
        inter_aligned = (direction == "BUY" and inter_score > 0.1) or \
                        (direction == "SELL" and inter_score < -0.1)
        if inter_aligned:
            tier_score += 0.10
        if ml_proba and ml_proba >= 0.55:
            tier_score += 0.08
        if neural_proba and neural_proba >= 0.55:
            tier_score += 0.05
        if tier_score >= 0.62:
            tier_lbl = "🅰️ <b>Convicción A</b> (alta)"
        elif tier_score >= 0.48:
            tier_lbl = "🅱️ <b>Convicción B</b> (media)"
        else:
            tier_lbl = "🅲 <b>Convicción C</b> (baja — informativa)"

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

        # Header diferenciado: SWING vs DAYTRADE vs INTRADAY vs REVERSIÓN
        mode = signal.get("mode", "")
        if mode == "SWING":
            hold_est  = signal.get("hold_estimate", "2-7 días")
            model_hdr = f"📅 <b>SWING TRADE</b>  —  hold estimado: <b>{hold_est}</b>\n"
            tf_header = "D1→H4"
            hdr_tag   = "SWING "
        elif mode == "DAYTRADE":
            model_hdr = "⚡ <b>DAYTRADE M15</b>  —  intradía, ejecución manual\n"
            tf_header = "H1→M15"
            hdr_tag   = "DAYTRADE "
        elif mode == "SCALP":
            model_hdr = "⚡ <b>SCALP M5</b>  —  intradía rápido, ejecución manual\n"
            tf_header = "M15→M5"
            hdr_tag   = "SCALP "
        elif signal.get("model") == "SWEEP_REVERSAL":
            model_hdr = "⚡ <b>MODELO: REVERSIÓN POR BARRIDO</b>\n"
            tf_header = "H1"
            hdr_tag   = ""
        else:
            model_hdr = ""
            tf_header = "H1"
            hdr_tag   = ""

        lines = [
            f"📊 <b>{hdr_tag}SETUP {symbol} | {tf_header} | {time_str}</b>",
            f"━━━━━━━━━━━━━━━━━━━━━━",
            f"{model_hdr}{dir_emoji} <b>{dir_es}</b>   ·   {tier_lbl}",
        ]

        # ── Bloque MI CUENTA PRIMERO (tu cuenta real — ejecución manual) ──
        # Va arriba para que el recorte de 4096 chars nunca lo toque.
        personal = signal.get("personal") or {}
        if personal:
            p_title = personal.get("title", "MI CUENTA")
            p_cur   = personal.get("currency", "EUR")
            lines += [f"",
                      f"💼 <b>{p_title} ({personal.get('equity', 0):,.0f} {p_cur}) "
                      f"— TU OPERACIÓN</b>",
                      f"━━━━━━━━━━━━━━━━━━━━━━"]
            if personal.get("apta"):
                p_sl_d  = abs(personal["entry"] - personal["sl"])
                p_tp1_r = abs(tp1 - personal["entry"]) / p_sl_d if p_sl_d else 0
                src     = "M15" if personal.get("refined") else "H1"
                lines += [
                    f"📍 <b>Entry:</b> <code>{personal['entry']:.2f}</code>  "
                    f"🛑 <b>SL:</b> <code>{personal['sl']:.2f}</code>  ({src})",
                    f"🎯 <b>TP1:</b> <code>{tp1:.2f}</code>  — R:R 1:{p_tp1_r:.1f}",
                ]
                if tp2 is not None:
                    p_tp2_r = abs(tp2 - personal["entry"]) / p_sl_d if p_sl_d else 0
                    lines.append(
                        f"🎯 <b>TP2:</b> <code>{tp2:.2f}</code>  — R:R 1:{p_tp2_r:.1f}")
                lines += [
                    f"📦 <b>Lotes: {personal['lots']:.2f}</b>  |  "
                    f"Riesgo: <b>{personal['risk_acc']:.2f} {p_cur}</b> "
                    f"({personal['risk_pct']:.1f}% equity)",
                    f"✅ <b>APTA {p_title}</b>",
                ]
            else:
                reason = "; ".join(personal.get("reasons", [])) or "no evaluable"
                lines += [
                    f"⚠️ <b>NO APTA</b> — {reason}",
                    f"<i>Señal solo informativa para tu cuenta personal</i>",
                ]

        # ── Bloque FundedNext 2K (ejecución manual) ───────────────
        funded = signal.get("funded") or {}
        if funded:
            f_title = funded.get("title", "FUNDEDNEXT 2K")
            lines += [f"", f"🏦 <b>{f_title} — TU OPERACIÓN</b>",
                      f"━━━━━━━━━━━━━━━━━━━━━━"]
            if funded.get("apta"):
                src     = "M15" if funded.get("refined") else "H1"
                f_sl_d  = abs(funded["entry"] - funded["sl"])
                f_tp1_r = abs(tp1 - funded["entry"]) / f_sl_d if f_sl_d else 0
                lines += [
                    f"📍 <b>Entry:</b> <code>{funded['entry']:.2f}</code>  "
                    f"🛑 <b>SL:</b> <code>{funded['sl']:.2f}</code>  ({src})",
                    f"🎯 <b>TP1:</b> <code>{tp1:.2f}</code>  — R:R 1:{f_tp1_r:.1f}",
                ]
                if tp2 is not None:
                    f_tp2_r = abs(tp2 - funded["entry"]) / f_sl_d if f_sl_d else 0
                    lines.append(
                        f"🎯 <b>TP2:</b> <code>{tp2:.2f}</code>  — R:R 1:{f_tp2_r:.1f}")
                lines += [
                    f"📦 <b>Lotes: {funded['lots']:.2f}</b>  |  "
                    f"Riesgo: <b>${funded['risk_usd']:.2f}</b> ({funded['risk_pct']:.1f}% equity)",
                    f"✅ <b>APTA 2K</b>  |  Room DD: ${funded['room']:.0f} "
                    f"(floor ${funded['floor']:,.0f})",
                ]
                if funded.get("room_warn"):
                    lines.append(
                        f"⚠️ Este trade consume el {funded['room_pct']:.0f}% del room restante"
                    )
            else:
                reason = "; ".join(funded.get("reasons", [])) or "no evaluable"
                lines += [
                    f"⚠️ <b>NO APTA 2K</b> — {reason}",
                    f"<i>Señal solo informativa para la cuenta fondeada</i>",
                ]
            if signal.get("news_blackout"):
                lines.append(
                    f"📰 FundedNext: dato de alto impacto cerca — abrir/cerrar "
                    f"±5 min = solo cuenta el 40% del beneficio"
                )

        # ── Niveles de la demo (referencia del análisis) ──────────
        demo_tf = "H4" if mode == "SWING" else (
            "M15" if mode == "DAYTRADE" else ("M5" if mode == "SCALP" else "H1"))
        lines += [
            f"",
            f"📋 <b>Análisis ({demo_tf + ' — sin ejecución demo' if mode in ('DAYTRADE', 'SCALP') else 'demo ' + demo_tf}):</b>",
            f"📍 Entrada: <code>{entry:.5f}</code>",
            f"🛑 Stop Loss: <code>{sl:.5f}</code>  ({sl_dist:.5f})",
            f"🎯 TP1: <code>{tp1:.5f}</code>  — R:R 1:{tp1_rr:.1f}",
        ]

        if tp2 is not None:
            tp2_dist = abs(tp2 - entry)
            tp2_rr   = tp2_dist / sl_dist if sl_dist else 0
            lines.append(f"🎯 TP2: <code>{tp2:.5f}</code>  — R:R 1:{tp2_rr:.1f}")

        if lot_size is not None:
            lines.append(f"📦 Lotes demo: {lot_size:.2f}")

        regime_str = f" | {regime}" if regime and regime not in ("UNKNOWN", "") else ""
        ml_str     = f" | ML {ml_proba:.0%}" if ml_proba and ml_proba != 0.5 else ""
        nn_str     = f" | NN {neural_proba:.0%}" if neural_proba and neural_proba != 0.5 else ""
        max_cf     = signal.get("max_confluences", 16.5)
        bias_lbl   = "Bias H1" if mode == "DAYTRADE" else (
            "Bias M15" if mode == "SCALP" else "Bias H4")
        lines += [
            f"",
            f"⚡ <b>Confianza:</b> {confidence:.0%}  ({confluences}/{max_cf}{regime_str}{ml_str}{nn_str})",
            f"📈 <b>{bias_lbl}:</b>   {bias_h4}",
        ]

        if rsi_val is not None:
            lines.append(f"📉 <b>RSI:</b>       {rsi_val:.1f}  ({rsi_state})")

        # ── Contexto INTERMARKET (datos que el retail no mira) ────
        if inter:
            ry  = inter.get("real_yields") or {}
            cot = inter.get("cot") or {}
            rt  = inter.get("ratio") or {}
            im_lines = []
            if ry.get("trend") and ry["trend"] != "NEUTRAL":
                flecha = "↓" if ry["trend"] == "BEARISH" else "↑"
                im_lines.append(
                    f"  • Reales 10Y {flecha} → {'oro alcista' if ry['trend']=='BEARISH' else 'oro bajista'}")
            if cot.get("extreme") and cot["extreme"] != "NORMAL":
                im_lines.append(
                    f"  • COT {cot['extreme']} (pct {cot.get('percentile','?')})")
            elif cot.get("percentile") is not None:
                im_lines.append(
                    f"  • COT specs: pct {cot.get('percentile')} (net {cot.get('net','?')})")
            if rt.get("signal") and rt["signal"] != "NEUTRAL":
                im_lines.append(f"  • Oro/Plata {rt['signal']} (z {rt.get('zscore','?')})")
            if im_lines:
                align_txt = "✅ a favor" if inter_aligned else "⚪ neutral/contra"
                lines += [f"", f"🌍 <b>Intermarket</b> ({align_txt}):"] + im_lines

        if cf_details:
            lines += [f"", f"✅ <b>Confluencias:</b>"]
            for d in cf_details:
                lines.append(f"  • {d}")

        # ── Razonamiento ──────────────────────────────────────────
        if reasoning:
            lines += [f"", f"💡 <b>Por qué esta operación:</b>", reasoning]

        # ── Contexto de noticias ──────────────────────────────────
        if news_context:
            lines += [f"", news_context]

        if news_warn:
            lines += [f"", f"⚠️ <b>Precaución:</b> {news_warn}"]

        dd_warn = signal.get("dd_warning", "")
        if dd_warn:
            lines += [f"", dd_warn]

        exec_note = signal.get("exec_block_note", "")
        if exec_note:
            lines += [f"", exec_note]

        obs_note = signal.get("observation_note", "")
        if obs_note:
            lines += [f"", f"🔍 <b>MODO OBSERVACIÓN:</b> {obs_note}", f"<i>Esta señal NO se ha ejecutado en MT5</i>"]

        lines += [f"", f"─────────────────────────", f"⚠️ No es consejo financiero. Gestiona tu riesgo."]

        return "\n".join(lines)

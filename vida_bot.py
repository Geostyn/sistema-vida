"""
vida_bot.py — Bot de Telegram para el Sistema de Vida Personal.

Recibe mensajes libres del usuario (dictado del día, gastos, ideas...)
y los procesa con Groq AI (GRATIS) para actualizar automáticamente los
archivos de Obsidian y responder con consejos de experto + XP.

Uso:
  python vida_bot.py

Requiere:
  - config.yaml con secciones telegram y groq
  - groq: pip install groq
  - requests (ya instalado del sistema de trading)
  - schedule (ya instalado)
"""

import os
import sys
import json
import yaml
import time
import logging
import requests
import schedule
import urllib3
from datetime import datetime
from pathlib import Path  # noqa: F401

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Logging ───────────────────────────────────────────────────
os.makedirs(os.path.join(os.path.dirname(__file__), "logs"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "logs", "vida_bot.log"),
            encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("vida_bot")

# ── Rutas ──────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
VAULT       = r"C:\Users\geost\Desktop\Obsidian SC Claude Code"
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.yaml")
VIDA_STATE  = os.path.join(VAULT, "ESTADO-VIDA.md")

sys.path.insert(0, SCRIPT_DIR)
try:
    from life_tracker import (
        add_deporte_entry, add_alimentacion_entry, add_lexico_entry,
        add_refran_entry, add_gasto_entry, add_idea_rapida,
        add_trading_reflexion, add_diario_entry,
    )
except Exception as _lt_err:
    logger.warning(f"life_tracker no disponible (modo cloud): {_lt_err}")
    add_deporte_entry = add_alimentacion_entry = add_lexico_entry = \
    add_refran_entry = add_gasto_entry = add_idea_rapida = \
    add_trading_reflexion = add_diario_entry = lambda *a, **kw: False
from vida_xp import (
    load_state, save_state, award_xp, update_streak,
    increment_counter, check_achievements,
    update_profile_file, get_xp_summary, xp_to_level, overall_level, SKILLS
)

# ── Config ─────────────────────────────────────────────────────
def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}

cfg             = load_config()
BOT_TOKEN       = os.environ.get("BOT_TOKEN")       or cfg.get("telegram", {}).get("bot_token", "")
CHAT_ID         = str(os.environ.get("CHAT_ID")     or cfg.get("telegram", {}).get("chat_id", ""))
GROUP_CHAT_ID   = str(os.environ.get("GROUP_CHAT_ID") or cfg.get("vida_bot", {}).get("group_chat_id", ""))
PLAYER_NAME     = os.environ.get("PLAYER_NAME")     or cfg.get("vida_bot", {}).get("player_name", "Jugador")
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY")    or cfg.get("groq", {}).get("api_key", "")
GROQ_MODEL      = os.environ.get("GROQ_MODEL")      or cfg.get("groq", {}).get("model", "llama-3.3-70b-versatile")
REMINDER_TIME   = os.environ.get("REMINDER_TIME")   or cfg.get("vida_bot", {}).get("reminder_time", "21:00")

# Cloud mode: activo cuando Supabase está configurado (Fly.io)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
IS_CLOUD     = bool(SUPABASE_URL and SUPABASE_KEY)

APP_URL        = os.environ.get("APP_URL", "")   # ej: https://sistema-vida-bot.onrender.com
SSL_VERIFY     = False
TELEGRAM_API   = f"https://api.telegram.org/bot{BOT_TOKEN}"
last_update_id = 0
last_entry_date = None


# ── Telegram helpers ───────────────────────────────────────────
def send_message(text: str, parse_mode: str = "HTML", chat_id: str = None) -> bool:
    target = chat_id or CHAT_ID
    try:
        resp = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": target, "text": text, "parse_mode": parse_mode},
            timeout=30, verify=SSL_VERIFY
        )
        return resp.ok
    except Exception as e:
        logger.error(f"Error enviando mensaje: {e}")
        return False


def broadcast_to_group(text: str):
    """Envía un mensaje al grupo de amigos (si está configurado)."""
    if GROUP_CHAT_ID and GROUP_CHAT_ID not in ("", "0", "None"):
        send_message(text, chat_id=GROUP_CHAT_ID)


def get_updates() -> list:
    global last_update_id
    try:
        resp = requests.get(
            f"{TELEGRAM_API}/getUpdates",
            params={"offset": last_update_id + 1, "timeout": 0},
            timeout=5, verify=SSL_VERIFY
        )
        data    = resp.json()
        updates = data.get("result", [])
        if updates:
            last_update_id = max(u["update_id"] for u in updates)
        return updates
    except Exception:
        return []


# ── Estado de vida ─────────────────────────────────────────────
def read_vida_state() -> str:
    if IS_CLOUD:
        try:
            from supabase_client import get_vida_state_summary
            return get_vida_state_summary()
        except Exception:
            pass
    try:
        return Path(VIDA_STATE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return "Sin datos previos del sistema de vida."


# ── Groq AI ────────────────────────────────────────────────────
SYSTEM_PROMPT = """Eres el asistente personal de vida del usuario. Tu rol es DUAL:
1. EXTRACTOR: Extraes información estructurada del mensaje del usuario
2. EXPERTO: Actúas como especialista en cada área y das consejos proactivos

OBJETIVO PRINCIPAL DEL USUARIO: Ganar masa muscular (targets: ~2.800 kcal/día, ~150g proteína, ~350g carbos, ~78g grasas, 3L agua)

ÁREAS DE EXPERTISE:
- 🥗 Nutricionista deportivo: calcula macros, evalúa si llega a proteína, sugiere ajustes
- 💪 Entrenador personal: sugiere progresiones, recuperación, volumen
- 🙏 Guía espiritual: celebra la conexión con Dios, anima las prácticas espirituales
- 💰 Asesor financiero: analiza gastos, progreso hacia ahorro
- 🚀 Business consultant: evalúa ideas, identifica oportunidades
- 📊 Analista de trading: comenta el rendimiento

FORMATO DE RESPUESTA: Devuelve SIEMPRE un JSON válido con EXACTAMENTE esta estructura:
{
  "updates": {
    "deporte": {"actividad": "", "duracion": "", "distancia": "", "sensacion": "😊", "notas": ""},
    "alimentacion": {"desayuno": "", "comida": "", "cena": "", "snacks": "", "kcal": "", "prot": "", "carbs": "", "grasas": "", "agua": "", "energia": "😊"},
    "lexico": [{"palabra": "", "definicion": "", "ejemplo": ""}],
    "refranes": [{"refran": "", "significado": "", "contexto": ""}],
    "gastos": [{"categoria": "", "concepto": "", "importe": 0.0}],
    "ideas": [{"idea": "", "inversion": "", "tiempo": "", "potencial": ""}],
    "trading": {"observacion": "", "tipo": "💡", "accion": ""},
    "habitos": {"ducha_fria": false, "te_clavo": false, "oracion": false, "silencio": false},
    "diario": {"lo_importante": "", "gratitud": "", "mejora": "", "habitos_ok": ""}
  },
  "response": "Tu respuesta en HTML de Telegram. Usa <b>negrita</b> y <i>cursiva</i>. Máximo 5 oraciones. Siempre incluye 1 consejo de experto relevante. Si el usuario menciona a Dios/fe, celébralo."
}

REGLAS IMPORTANTES:
- Solo incluye campos con datos reales (deja "" los que no apliquen, false los booleans sin datos)
- Categorías de gasto válidas: Comida, Transporte, Ocio, Ropa, Salud, Formación, Ahorro, Hogar, Extra
- Si mencionas proteína, SIEMPRE compara con el target de 150g/día
- Respuesta cálida, motivadora, como un coach amigo
- Responde siempre en ESPAÑOL"""


def call_groq(user_message: str, vida_state: str) -> dict:
    """Llama a Groq API (gratuito) y devuelve el JSON parseado."""
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)

        context = f"ESTADO DEL SISTEMA DE VIDA:\n{vida_state[:1500]}\n\n---\n\nMENSAJE DEL USUARIO:\n{user_message}"

        chat = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": context}
            ],
            max_tokens=1024,
            temperature=0.3,
        )

        text = chat.choices[0].message.content.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        # Extraer primer JSON válido
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start >= 0 and end > start:
            text = text[start:end]
        return json.loads(text)

    except ImportError:
        logger.error("groq no instalado. Ejecuta: pip install groq")
        return {"updates": {}, "response": "Error: instala groq con <code>pip install groq</code>"}
    except json.JSONDecodeError as e:
        logger.error(f"Error parseando JSON: {e}\nTexto: {text[:300]}")
        return {"updates": {}, "response": "✅ Mensaje recibido (procesando respuesta...)"}
    except Exception as e:
        logger.error(f"Error en Groq: {e}")
        return {"updates": {}, "response": f"Error de conexión con Groq: {e}"}


# ── Aplicar updates a Obsidian + XP ───────────────────────────
def apply_updates(updates: dict) -> tuple[list[str], list[str]]:
    """Aplica updates a Obsidian (local) o Supabase (cloud) y premia XP."""
    files_updated = []
    xp_messages   = []
    xp_state      = load_state()

    if IS_CLOUD:
        import supabase_client as sb

    dep = updates.get("deporte", {})
    if dep.get("actividad"):
        if IS_CLOUD:
            ok = sb.insert_deporte(
                dep.get("actividad", ""), dep.get("duracion", ""),
                dep.get("distancia", ""), dep.get("sensacion", "😊"),
                dep.get("notas", ""))
        else:
            ok = add_deporte_entry(
                dep.get("actividad", ""), dep.get("duracion", ""),
                dep.get("distancia", ""), dep.get("sensacion", "😊"),
                dep.get("notas", ""))
        if ok:
            files_updated.append("🏋️ Deporte")
            result = award_xp("deporte_registrado", xp_state)
            xp_messages.extend(result["messages"])
            xp_state = result["state"]
            increment_counter("entrenamientos", xp_state)
            xp_state = load_state()

    alim = updates.get("alimentacion", {})
    if any(alim.get(k) for k in ["desayuno", "comida", "cena", "kcal", "prot"]):
        args = (
            alim.get("desayuno", ""), alim.get("comida", ""),
            alim.get("cena", ""),    alim.get("snacks", ""),
            alim.get("kcal", ""),    alim.get("prot", ""),
            alim.get("carbs", ""),   alim.get("grasas", ""),
            alim.get("agua", ""),    alim.get("energia", "😊")
        )
        ok = sb.insert_alimentacion(*args) if IS_CLOUD else add_alimentacion_entry(*args)
        if ok:
            files_updated.append("🍽️ Alimentación")
            result = award_xp("alimentacion_registrada", xp_state)
            xp_messages.extend(result["messages"])
            xp_state = result["state"]
            try:
                prot_val = float(str(alim.get("prot", "0")).replace("g", "").strip() or 0)
                if prot_val >= 140:
                    result2 = award_xp("proteina_objetivo", xp_state)
                    xp_messages.extend(result2["messages"])
                    xp_state = result2["state"]
                    update_streak("proteina", True, xp_state)
                    xp_state = load_state()
            except (ValueError, TypeError):
                pass

    for lex in updates.get("lexico", []):
        if lex.get("palabra"):
            if IS_CLOUD:
                sb.insert_lexico(lex["palabra"], lex.get("definicion", ""), lex.get("ejemplo", ""))
            else:
                add_lexico_entry(lex["palabra"], lex.get("definicion", ""), lex.get("ejemplo", ""))
            files_updated.append("📖 Léxico")
            result = award_xp("lexico_palabra", xp_state)
            xp_messages.extend(result["messages"])
            xp_state = result["state"]
            increment_counter("lexico", xp_state)
            xp_state = load_state()

    for ref in updates.get("refranes", []):
        if ref.get("refran"):
            if IS_CLOUD:
                sb.insert_refran(ref["refran"], ref.get("significado", ""), ref.get("contexto", ""))
            else:
                add_refran_entry(ref["refran"], ref.get("significado", ""), ref.get("contexto", ""))
            files_updated.append("💬 Refrán")
            result = award_xp("refran_aprendido", xp_state)
            xp_messages.extend(result["messages"])
            xp_state = result["state"]
            increment_counter("refranes", xp_state)
            xp_state = load_state()

    for gasto in updates.get("gastos", []):
        if gasto.get("concepto") and gasto.get("importe"):
            if IS_CLOUD:
                sb.insert_gasto(gasto.get("categoria", "Extra"), gasto["concepto"], float(gasto["importe"]))
            else:
                add_gasto_entry(gasto.get("categoria", "Extra"), gasto["concepto"], float(gasto["importe"]))
            files_updated.append(f"💶 Gasto €{gasto['importe']}")
            result = award_xp("gasto_registrado", xp_state)
            xp_messages.extend(result["messages"])
            xp_state = result["state"]

    for idea in updates.get("ideas", []):
        if idea.get("idea"):
            if IS_CLOUD:
                sb.insert_idea(idea["idea"], idea.get("inversion", "—"),
                               idea.get("tiempo", "—"), idea.get("potencial", "—"))
            else:
                add_idea_rapida(idea["idea"], idea.get("inversion", "—"),
                                idea.get("tiempo", "—"), idea.get("potencial", "—"))
            files_updated.append("💼 Idea")
            result = award_xp("idea_nueva", xp_state)
            xp_messages.extend(result["messages"])
            xp_state = result["state"]
            increment_counter("ideas", xp_state)
            xp_state = load_state()

    trad = updates.get("trading", {})
    if trad.get("observacion"):
        if not IS_CLOUD:
            add_trading_reflexion(trad["observacion"], trad.get("tipo", "💡"), trad.get("accion", "—"))
        files_updated.append("📈 Trading")
        result = award_xp("reflexion_trading", xp_state)
        xp_messages.extend(result["messages"])
        xp_state = result["state"]

    habitos = updates.get("habitos", {})
    all_habits_done = True
    if habitos.get("ducha_fria"):
        result = award_xp("ducha_fria", xp_state)
        xp_messages.extend(result["messages"])
        xp_state = result["state"]
        update_streak("ducha_fria", True, xp_state)
        xp_state = load_state()
    else:
        all_habits_done = False

    if habitos.get("te_clavo"):
        result = award_xp("te_clavo", xp_state)
        xp_messages.extend(result["messages"])
        xp_state = result["state"]
        update_streak("te_clavo", True, xp_state)
        xp_state = load_state()
    else:
        all_habits_done = False

    if habitos.get("oracion"):
        result = award_xp("oracion_cumplida", xp_state)
        xp_messages.extend(result["messages"])
        xp_state = result["state"]
        update_streak("oracion", True, xp_state)
        xp_state = load_state()
    else:
        all_habits_done = False

    if habitos.get("silencio"):
        result = award_xp("silencio_cumplido", xp_state)
        xp_messages.extend(result["messages"])
        xp_state = result["state"]

    if all_habits_done and any(habitos.values()):
        update_streak("habitos_todos", True, xp_state)
        xp_state = load_state()
    elif any(not v for v in habitos.values()):
        update_streak("habitos_todos", False, xp_state)
        xp_state = load_state()

    diario = updates.get("diario", {})
    if any(diario.get(k) for k in ["lo_importante", "gratitud", "mejora"]):
        add_diario_entry(
            diario.get("lo_importante", ""), diario.get("gratitud", ""),
            diario.get("mejora", ""), diario.get("habitos_ok", "")
        )
        files_updated.append("📖 Diario")
        result = award_xp("diario_escrito", xp_state)
        xp_messages.extend(result["messages"])
        xp_state = result["state"]
        increment_counter("dias_diario", xp_state)
        xp_state = load_state()

    # Guardar hábitos del día en Supabase
    if IS_CLOUD and any(habitos.values()):
        sb.insert_habitos(
            habitos.get("ducha_fria", False), habitos.get("te_clavo", False),
            habitos.get("oracion", False),    habitos.get("silencio", False),
        )

    # Comprobar logros pendientes
    ach_msgs = check_achievements(xp_state)
    xp_messages.extend(ach_msgs)
    save_state(xp_state)

    # Sincronizar XP a Supabase si estamos en cloud
    if IS_CLOUD:
        from supabase_client import sync_xp_to_supabase
        sync_xp_to_supabase(os.path.join(SCRIPT_DIR, "vida_xp.json"))

    # Actualizar perfil en Obsidian (solo local)
    if not IS_CLOUD:
        update_profile_file(xp_state)

    # Broadcast al grupo de amigos: level-ups y logros
    _broadcast_highlights(xp_messages, xp_state)

    return files_updated, xp_messages


def _broadcast_highlights(xp_messages: list[str], xp_state: dict):
    """Envía al grupo de Telegram los level-ups y logros importantes."""
    if not GROUP_CHAT_ID or GROUP_CHAT_ID in ("", "0", "None"):
        return

    highlights = [m for m in xp_messages if any(k in m for k in ["LEVEL UP", "LOGRO", "NIVEL GLOBAL"])]
    if not highlights:
        return

    ov_level, ov_name = overall_level(xp_state)
    text = (
        f"🎮 <b>{PLAYER_NAME}</b> acaba de subir de nivel:\n\n"
        + "\n".join(highlights)
        + f"\n\n⚔️ Nivel {ov_level} — {ov_name} · {xp_state['total_xp']:,} XP total"
    )
    broadcast_to_group(text)


# ── Procesar mensaje ───────────────────────────────────────────
def process_message(text: str):
    global last_entry_date

    lower = text.strip().lower()

    if lower in ["/start", "/hola", "/ayuda", "/help"]:
        xp_state = load_state()
        ov_level, ov_name = overall_level(xp_state)
        send_message(
            f"👋 <b>¡Hola! Soy tu asistente de vida.</b>\n\n"
            f"⚔️ Tu nivel actual: <b>{ov_level} — {ov_name}</b> · {xp_state['total_xp']:,} XP\n\n"
            "Cuéntame tu día y lo organizo todo + te doy XP:\n"
            "• Lo que comiste 🍽️ · Tu entreno 💪 · Gastos 💶\n"
            "• Hábitos cumplidos ✅ · Ideas de negocio 💡\n"
            "• Reflexiones ✝️🙏\n\n"
            "<b>Comandos:</b>\n"
            "/perfil — Tu ficha de personaje y XP\n"
            "/logros — Ver logros desbloqueados\n"
            "/reto — Retar a tus amigos en el grupo\n"
            "/estado — Estado del sistema de vida"
        )
        return

    if lower == "/perfil":
        xp_state = load_state()
        ov_level, ov_name = overall_level(xp_state)
        skills_text = ""
        for sk_id, sk_info in SKILLS.items():
            sk_data = xp_state["skills"].get(sk_id, {"xp": 0, "level": 1})
            _, sk_lvl_name, sk_in, sk_next = xp_to_level(sk_data["xp"])
            bar_f = int((sk_in / sk_next * 8)) if sk_next else 8
            bar   = "█" * bar_f + "░" * (8 - bar_f)
            skills_text += f"  {sk_info['emoji']} {sk_info['nombre']}: Nv.{sk_data['level']} [{bar}]\n"

        send_message(
            f"⚔️ <b>PERFIL DEL AVENTURERO</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌟 Nivel Global: <b>{ov_level} — {ov_name}</b>\n"
            f"✨ XP Total: <b>{xp_state['total_xp']:,}</b>\n"
            f"🏆 Logros: {len(xp_state.get('achievements_unlocked', []))}\n\n"
            f"<b>Habilidades:</b>\n{skills_text}\n"
            f"Ver perfil completo: abre <code>perfil-aventurero.md</code> en Obsidian"
        )
        return

    if lower == "/logros":
        xp_state = load_state()
        unlocked = xp_state.get("achievements_unlocked", [])
        from vida_xp import ACHIEVEMENTS
        unlocked_text = "\n".join(
            f"  ✅ {ACHIEVEMENTS[a]['emoji']} {ACHIEVEMENTS[a]['nombre']}"
            for a in unlocked
        ) or "  (ninguno aún — ¡empieza hoy!)"
        total = len(ACHIEVEMENTS)
        send_message(
            f"🏆 <b>LOGROS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Desbloqueados: {len(unlocked)}/{total}\n\n"
            f"{unlocked_text}\n\n"
            f"<i>Ver todos en perfil-aventurero.md</i>"
        )
        return

    if lower == "/reto":
        xp_state = load_state()
        ov_level, ov_name = overall_level(xp_state)
        reto_msg = (
            f"⚔️ <b>{PLAYER_NAME} te reta a competir!</b>\n\n"
            f"🌟 Nivel: {ov_level} — {ov_name}\n"
            f"✨ XP Total: {xp_state['total_xp']:,}\n"
            f"🏆 Logros: {len(xp_state.get('achievements_unlocked', []))}\n\n"
            f"¿Puedes superarme? 🔥\n"
            f"Instala el sistema y demuéstralo."
        )
        broadcast_to_group(reto_msg)
        send_message("⚔️ Reto enviado al grupo. ¡Que empiece la competencia!")
        return

    if lower == "/estado":
        state = read_vida_state()
        if len(state) > 3000:
            state = state[:3000] + "\n...(truncado)"
        send_message(f"<b>Estado del Sistema de Vida:</b>\n\n<pre>{state}</pre>")
        return

    # Procesar mensaje libre con IA
    send_message("⏳ Analizando tu día...")

    try:
        vida_state = read_vida_state()
        result     = call_groq(text, vida_state)
        updates    = result.get("updates", {})
        response   = result.get("response", "✅ Recibido.")

        files_updated, xp_messages = apply_updates(updates)
        last_entry_date = datetime.now().date()

        # Respuesta principal
        final_reply = response
        if files_updated:
            final_reply += f"\n\n<i>📁 Guardado: {' · '.join(files_updated)}</i>"

        send_message(final_reply)

        # Mensajes de XP (si los hay)
        if xp_messages:
            xp_text  = "\n".join(xp_messages)
            xp_state = load_state()
            xp_summary = get_xp_summary(xp_state)
            send_message(
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎮 <b>PUNTOS DE EXPERIENCIA</b>\n\n"
                f"{xp_text}\n\n"
                f"<i>{xp_summary}</i>"
            )
    except Exception as e:
        logger.error(f"Error en process_message: {e}", exc_info=True)
        send_message(f"❌ Error procesando tu mensaje: <code>{type(e).__name__}: {e}</code>")


# ── Recordatorio diario ────────────────────────────────────────
def send_daily_reminder():
    global last_entry_date
    today = datetime.now().date()
    if last_entry_date != today:
        xp_state  = load_state()
        ov_level, ov_name = overall_level(xp_state)
        send_message(
            f"🔔 <b>Recordatorio del sistema de vida</b>\n\n"
            f"⚔️ Nivel {ov_level} — {ov_name} | {xp_state['total_xp']:,} XP\n\n"
            "¿Ya registraste tu día? Cuéntame:\n"
            "• Qué comiste 🍽️ (macros si los tienes)\n"
            "• Si entrenaste 💪\n"
            "• Gastos del día 💶\n"
            "• Hábitos: ducha fría 🚿, té clavo 🌿, oración 🙏\n"
            "• Algo que agradeces ✨\n\n"
            "<i>Cada acción te da XP. ¡El nivel no para de subir!</i>"
        )


# ── Transcripción de audio (Groq Whisper — gratuito) ─────────
def transcribe_voice(file_id: str) -> str:
    """Descarga el audio de Telegram y lo transcribe con Groq Whisper."""
    try:
        # Obtener ruta del archivo en Telegram
        r = requests.get(
            f"{TELEGRAM_API}/getFile",
            params={"file_id": file_id},
            verify=SSL_VERIFY, timeout=10,
        )
        file_path = r.json()["result"]["file_path"]

        # Descargar audio
        audio_url  = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        audio_data = requests.get(audio_url, verify=SSL_VERIFY, timeout=30).content

        # Transcribir con Groq Whisper
        from groq import Groq
        client        = Groq(api_key=GROQ_API_KEY)
        transcription = client.audio.transcriptions.create(
            file=("audio.ogg", audio_data),
            model="whisper-large-v3",
            language="es",
        )
        text = transcription.text.strip()
        logger.info(f"Audio transcrito: {text[:80]}")
        return text
    except Exception as e:
        logger.error(f"Error transcribiendo audio: {e}")
        return ""


# ── Webhook Flask (modo cloud / Render) ───────────────────────
def _make_flask_app():
    from flask import Flask, request, jsonify
    flask_app = Flask(__name__)

    @flask_app.route("/webhook", methods=["POST"])
    def webhook():
        data = request.get_json(force=True, silent=True) or {}
        msg  = data.get("message", {})
        text = msg.get("text", "").strip()

        # Soporte de mensajes de voz
        if not text and msg.get("voice"):
            send_message("🎤 Transcribiendo tu audio...")
            text = transcribe_voice(msg["voice"]["file_id"])
            if not text:
                send_message("❌ No pude transcribir el audio. Intenta enviarlo como texto.")

        if str(msg.get("chat", {}).get("id", "")) == CHAT_ID and text:
            import threading
            threading.Thread(target=process_message, args=(text,), daemon=True).start()
        return jsonify({"ok": True})

    @flask_app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "bot": "vida-bot"})

    return flask_app


def _schedule_loop():
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
        time.sleep(60)


# ── Loop principal ─────────────────────────────────────────────
def main():
    if not BOT_TOKEN or BOT_TOKEN == "TU_BOT_TOKEN":
        logger.error("Bot token no configurado → telegram.bot_token")
        sys.exit(1)
    if not GROQ_API_KEY or GROQ_API_KEY.startswith("gsk_TU"):
        logger.error("Groq API key no configurada → groq.api_key")
        sys.exit(1)

    logger.info("🤖 Vida Bot iniciado (Groq AI — gratuito)")
    if IS_CLOUD:
        logger.info("☁️  Modo cloud activo — datos en Supabase")
        from supabase_client import sync_xp_from_supabase
        sync_xp_from_supabase(os.path.join(SCRIPT_DIR, "vida_xp.json"))
    xp_state       = load_state()
    ov_level, ov_name = overall_level(xp_state)

    schedule.every().day.at(REMINDER_TIME).do(send_daily_reminder)

    import threading

    def _background_init():
        """Configura webhook o inicia polling según APP_URL. Siempre en background."""
        try:
            if APP_URL:
                # Webhook: registrar URL con Telegram
                resp = requests.get(
                    f"{TELEGRAM_API}/setWebhook",
                    params={"url": f"{APP_URL}/webhook", "drop_pending_updates": True},
                    verify=SSL_VERIFY, timeout=15,
                )
                logger.info(f"Webhook registrado: {resp.json()}")
            else:
                # Polling local: bucle en este mismo thread de background
                logger.info("💻 Modo polling local")
                while True:
                    try:
                        for update in get_updates():
                            msg  = update.get("message", {})
                            text = msg.get("text", "").strip()
                            if str(msg.get("chat", {}).get("id", "")) == CHAT_ID and text:
                                process_message(text)
                        schedule.run_pending()
                        time.sleep(2)
                    except Exception as e:
                        logger.error(f"Polling error: {e}")
                        time.sleep(10)
                return  # nunca llega aquí en polling

            send_message(
                f"🟢 <b>VIDA BOT — ACTIVO</b>\n"
                f"⚔️ Nivel: {ov_level} — {ov_name} · {xp_state['total_xp']:,} XP\n"
                f"Recordatorio a las {REMINDER_TIME}. Escribe /ayuda."
            )
            threading.Thread(target=_schedule_loop, daemon=True).start()
        except Exception as e:
            logger.error(f"Background init error: {e}")

    # Flask SIEMPRE arranca — da el /health que Render y UptimeRobot necesitan
    port = int(os.environ.get("PORT", 8080))
    flask_app = _make_flask_app()
    threading.Thread(target=_background_init, daemon=True).start()
    logger.info(f"🌐 Flask arrancando en puerto {port}")
    flask_app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()

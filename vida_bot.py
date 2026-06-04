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
GRUPO_FAMILIA_CHAT_ID = str(
    os.environ.get("GRUPO_FAMILIA_CHAT_ID") or
    cfg.get("vida_bot", {}).get("grupo_familia_chat_id", "")
)
SSL_VERIFY     = False
TELEGRAM_API   = f"https://api.telegram.org/bot{BOT_TOKEN}"
last_update_id = 0
last_entry_date = None

# ── Perfil nutricional ─────────────────────────────────────────
PROFILE_STEPS = [
    {"key": "peso",        "pregunta": "⚖️ <b>¿Cuánto pesas en kg?</b>\nEj: 75"},
    {"key": "altura",      "pregunta": "📏 <b>¿Cuánto mides en cm?</b>\nEj: 178"},
    {"key": "edad",        "pregunta": "🎂 <b>¿Cuántos años tienes?</b>"},
    {"key": "tipo_cuerpo", "pregunta": "💪 <b>¿Cuál es tu tipo de cuerpo?</b>\nResponde: <code>ectomorfo</code>, <code>mesomorfo</code> o <code>endomorfo</code>"},
    {"key": "dias_entreno","pregunta": "🏋️ <b>¿Cuántos días por semana entrenas?</b>\nResponde un número del 0 al 7"},
    {"key": "intolerancias","pregunta": "🥗 <b>¿Tienes alguna intolerancia alimentaria?</b>\nEj: lactosa, gluten, ninguna"},
]

_profile_conv: dict = {}
_perfil_cache: dict = {}

ACTIVITY_FACTORS = {0: 1.2, 1: 1.375, 2: 1.375, 3: 1.55, 4: 1.55, 5: 1.725, 6: 1.725, 7: 1.9}
PERFIL_FILE = os.path.join(SCRIPT_DIR, "perfil_usuario.json")


def _calc_targets(peso: float, altura: float, edad: int, tipo_cuerpo: str, dias: int) -> dict:
    """Calcula BMR → TDEE → macros para ganancia muscular."""
    bmr = (10 * peso) + (6.25 * altura) - (5 * edad) + 5
    factor = ACTIVITY_FACTORS.get(min(dias, 7), 1.55)
    tdee = bmr * factor
    surplus = 500 if tipo_cuerpo == "ectomorfo" else 400
    target_kcal = round(tdee + surplus)
    prot_mult = 2.4 if tipo_cuerpo == "ectomorfo" else 2.2
    target_prot = round(peso * prot_mult)
    target_grasas = round(target_kcal * 0.25 / 9)
    target_carbs = round((target_kcal - target_prot * 4 - target_grasas * 9) / 4)
    return {
        "tdee": round(tdee),
        "target_kcal": target_kcal,
        "target_prot": target_prot,
        "target_carbs": target_carbs,
        "target_grasas": target_grasas,
    }


def load_perfil() -> dict:
    global _perfil_cache
    if _perfil_cache:
        return _perfil_cache
    if IS_CLOUD:
        try:
            import supabase_client as sb
            _perfil_cache = sb.get_perfil()
            return _perfil_cache
        except Exception:
            pass
    try:
        with open(PERFIL_FILE, "r", encoding="utf-8") as f:
            _perfil_cache = json.load(f)
        return _perfil_cache
    except FileNotFoundError:
        return {}


def save_perfil_local(data: dict):
    global _perfil_cache
    _perfil_cache = data
    with open(PERFIL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if IS_CLOUD:
        try:
            import supabase_client as sb
            sb.save_perfil(data)
        except Exception:
            pass


def get_macros_hoy() -> dict:
    if IS_CLOUD:
        try:
            import supabase_client as sb
            return sb.get_macros_hoy()
        except Exception:
            pass
    return {"kcal": 0, "prot_g": 0, "carbs_g": 0, "grasas_g": 0, "agua_l": 0}


def _macro_progress_msg(perfil: dict) -> str:
    """Genera mensaje de progreso de macros del día vs targets del perfil."""
    hoy = get_macros_hoy()
    tk = perfil.get("target_kcal", 2800)
    tp = perfil.get("target_prot", 150)
    tc = perfil.get("target_carbs", 350)
    tg = perfil.get("target_grasas", 78)

    def barra(actual, target):
        pct = min(actual / target, 1.0) if target else 0
        filled = int(pct * 8)
        return "█" * filled + "░" * (8 - filled)

    kcal_r = hoy["kcal"]; prot_r = hoy["prot_g"]
    carbs_r = hoy["carbs_g"]; grasas_r = hoy["grasas_g"]

    msg = (
        "\n\n━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>PROGRESO DEL DÍA</b>\n"
        f"🔥 Kcal:  {kcal_r:.0f}/{tk} [{barra(kcal_r,tk)}] faltan {max(tk-kcal_r,0):.0f}\n"
        f"💪 Prot:  {prot_r:.0f}/{tp}g [{barra(prot_r,tp)}] faltan {max(tp-prot_r,0):.0f}g\n"
        f"🌾 Carbs: {carbs_r:.0f}/{tc}g [{barra(carbs_r,tc)}] faltan {max(tc-carbs_r,0):.0f}g\n"
        f"🥑 Grasas:{grasas_r:.0f}/{tg}g [{barra(grasas_r,tg)}] faltan {max(tg-grasas_r,0):.0f}g"
    )
    return msg


def _recomendacion_comida(perfil: dict) -> str:
    """Genera una recomendación específica de qué comer para completar el día."""
    hoy = get_macros_hoy()
    prot_falta = max(perfil.get("target_prot", 150) - hoy["prot_g"], 0)
    kcal_falta = max(perfil.get("target_kcal", 2800) - hoy["kcal"], 0)
    carbs_falta = max(perfil.get("target_carbs", 350) - hoy["carbs_g"], 0)
    intol = (perfil.get("intolerancias") or "ninguna").lower()

    if prot_falta < 10 and kcal_falta < 100:
        return "\n\n✅ <b>¡Targets alcanzados hoy! Buen trabajo.</b>"

    sugerencias = []
    if prot_falta >= 20:
        if "lactosa" not in intol:
            sugerencias.append(f"• {round(prot_falta/0.25)}g de queso cottage (proteína: ~{round(prot_falta)}g)")
        sugerencias.append(f"• {round(prot_falta/0.31)}g de pechuga de pollo (proteína: ~{round(prot_falta)}g)")
    if kcal_falta >= 200 and carbs_falta >= 30:
        sugerencias.append(f"• {round(carbs_falta/0.28)}g de arroz cocido (~{round(kcal_falta*0.5)}kcal)")
    if not sugerencias:
        sugerencias.append(f"• Un snack de ~{round(kcal_falta)}kcal (fruta + frutos secos)")

    recs = "\n".join(sugerencias[:3])
    return f"\n\n🍽️ <b>Para completar el día puedes comer:</b>\n{recs}"


def _handle_profile_step(text: str) -> bool:
    """Gestiona el flujo conversacional del perfil. Devuelve True si procesó el mensaje."""
    if not _profile_conv:
        return False

    step_idx = _profile_conv.get("step", 0)
    if step_idx >= len(PROFILE_STEPS):
        return False

    field = PROFILE_STEPS[step_idx]["key"]
    val = text.strip()

    # Validación básica
    if field in ("peso", "altura", "edad", "dias_entreno"):
        try:
            val = float(val.replace(",", ".").replace("kg", "").replace("cm", "").strip())
            if field == "edad":
                val = int(val)
            elif field == "dias_entreno":
                val = max(0, min(7, int(val)))
        except ValueError:
            send_message(f"⚠️ Necesito un número. {PROFILE_STEPS[step_idx]['pregunta']}")
            return True

    if field == "tipo_cuerpo":
        val = val.lower()
        if val not in ("ectomorfo", "mesomorfo", "endomorfo"):
            send_message("⚠️ Responde <code>ectomorfo</code>, <code>mesomorfo</code> o <code>endomorfo</code>")
            return True

    _profile_conv["data"][field] = val
    _profile_conv["step"] = step_idx + 1

    if _profile_conv["step"] < len(PROFILE_STEPS):
        send_message(PROFILE_STEPS[_profile_conv["step"]]["pregunta"])
        return True

    # Perfil completo — calcular y guardar
    d = _profile_conv["data"]
    targets = _calc_targets(
        float(d["peso"]), float(d["altura"]), int(d["edad"]),
        d["tipo_cuerpo"], int(d["dias_entreno"])
    )
    perfil = {**d, **targets}
    save_perfil_local(perfil)
    _profile_conv.clear()

    tipo = d["tipo_cuerpo"].capitalize()
    send_message(
        f"✅ <b>Perfil guardado!</b> 🎯\n\n"
        f"📋 <b>Tu perfil:</b>\n"
        f"  ⚖️ {d['peso']} kg · 📏 {d['altura']} cm · 🎂 {d['edad']} años\n"
        f"  💪 {tipo} · 🏋️ {d['dias_entreno']} días/semana\n\n"
        f"🎯 <b>Tus targets personalizados (ganancia muscular):</b>\n"
        f"  🔥 Calorías: <b>{perfil['target_kcal']} kcal/día</b>\n"
        f"  💪 Proteína: <b>{perfil['target_prot']}g/día</b>\n"
        f"  🌾 Carbos:   <b>{perfil['target_carbs']}g/día</b>\n"
        f"  🥑 Grasas:   <b>{perfil['target_grasas']}g/día</b>\n"
        f"  💧 Agua:     <b>3L/día</b>\n\n"
        f"<i>TDEE calculado: {perfil['tdee']} kcal · Superávit: +{perfil['target_kcal']-perfil['tdee']} kcal</i>\n"
        f"A partir de ahora, cuando menciones comida te diré cuánto llevas y qué te falta. 💪"
    )
    return True


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
BASE_SYSTEM_PROMPT = """Eres el asistente personal de vida del usuario. Tu rol es DUAL:
1. EXTRACTOR: Extraes información estructurada del mensaje del usuario
2. EXPERTO: Actúas como especialista en cada área y das consejos proactivos

ÁREAS DE EXPERTISE:
- 🥗 Nutricionista deportivo: SIEMPRE que el usuario mencione comida, estima los macros de ESA comida concreta (kcal, proteína, carbos, grasas) con los valores más representativos según las cantidades mencionadas. Si no se dan cantidades exactas, estima porciones normales. Rellena "kcal", "prot", "carbs", "grasas" en el JSON con números (solo el número, sin unidades).
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
  "response": "Tu respuesta en HTML de Telegram. Usa <b>negrita</b> y <i>cursiva</i>. Si mencionas comida, incluye los macros estimados de ESA comida en la respuesta. Máximo 5 oraciones. Siempre incluye 1 consejo de experto. Si el usuario menciona a Dios/fe, celébralo."
}

REGLAS IMPORTANTES:
- Solo incluye campos con datos reales (deja "" los que no apliquen, false los booleans sin datos)
- Categorías de gasto válidas: Comida, Transporte, Ocio, Ropa, Salud, Formación, Ahorro, Hogar, Extra
- Cuando haya datos de alimentación, SIEMPRE rellena kcal/prot/carbs/grasas con tu mejor estimación numérica
- Respuesta cálida, motivadora, como un coach amigo
- Responde siempre en ESPAÑOL"""


def build_system_prompt() -> str:
    """Construye el system prompt con los targets del perfil del usuario."""
    perfil = load_perfil()
    if perfil.get("target_kcal"):
        targets = (
            f"OBJETIVO DEL USUARIO: Ganar masa muscular\n"
            f"TARGETS PERSONALIZADOS: {perfil['target_kcal']} kcal/día · "
            f"{perfil['target_prot']}g proteína · {perfil['target_carbs']}g carbos · "
            f"{perfil['target_grasas']}g grasas · 3L agua\n"
            f"TIPO DE CUERPO: {perfil.get('tipo_cuerpo','').capitalize()}"
        )
    else:
        targets = "OBJETIVO DEL USUARIO: Ganar masa muscular (targets aprox: 2800kcal · 150g prot · 350g carbos · 78g grasas · 3L agua)"
    return targets + "\n\n" + BASE_SYSTEM_PROMPT


SYSTEM_PROMPT = BASE_SYSTEM_PROMPT  # compatibilidad — se sobreescribe en call_groq


def call_groq(user_message: str, vida_state: str) -> dict:
    """Llama a Groq API (gratuito) y devuelve el JSON parseado."""
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)

        system_prompt = build_system_prompt()
        context = f"ESTADO DEL SISTEMA DE VIDA:\n{vida_state[:1500]}\n\n---\n\nMENSAJE DEL USUARIO:\n{user_message}"

        chat = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
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
    alim_guardada = False
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
            alim_guardada = True
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

    return files_updated, xp_messages, alim_guardada


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

    # Si hay un flujo de perfil activo, capturar la respuesta antes que nada
    if _profile_conv and text.strip().lower() not in ("/perfil", "/perfil actualizar"):
        if _handle_profile_step(text):
            return

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
        perfil = load_perfil()
        if perfil.get("peso"):
            # Mostrar perfil actual y preguntar si quiere actualizarlo
            tipo = perfil.get("tipo_cuerpo", "—").capitalize()
            send_message(
                f"📋 <b>TU PERFIL NUTRICIONAL</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚖️ Peso: <b>{perfil['peso']} kg</b>  📏 Altura: <b>{perfil['altura']} cm</b>\n"
                f"🎂 Edad: <b>{perfil['edad']} años</b>  💪 Tipo: <b>{tipo}</b>\n"
                f"🏋️ Entrena: <b>{perfil['dias_entreno']} días/semana</b>\n"
                f"🥗 Intolerancias: <b>{perfil.get('intolerancias','ninguna')}</b>\n\n"
                f"🎯 <b>Targets personalizados:</b>\n"
                f"  🔥 {perfil.get('target_kcal','—')} kcal · 💪 {perfil.get('target_prot','—')}g prot\n"
                f"  🌾 {perfil.get('target_carbs','—')}g carbos · 🥑 {perfil.get('target_grasas','—')}g grasas\n\n"
                f"Para actualizar escribe <code>/perfil actualizar</code>"
            )
            return
        # Sin perfil → iniciar flujo
        _profile_conv.clear()
        _profile_conv["step"] = 0
        _profile_conv["data"] = {}
        send_message(
            "🎯 <b>Vamos a personalizar tu plan nutricional!</b>\n\n"
            "Te haré 6 preguntas rápidas para calcular tus macros exactos según tu cuerpo.\n\n"
            + PROFILE_STEPS[0]["pregunta"]
        )
        return

    if lower == "/perfil actualizar":
        _profile_conv.clear()
        _profile_conv["step"] = 0
        _profile_conv["data"] = {}
        send_message("✏️ <b>Actualizando tu perfil...</b>\n\n" + PROFILE_STEPS[0]["pregunta"])
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

        files_updated, xp_messages, alim_guardada = apply_updates(updates)
        last_entry_date = datetime.now().date()

        # Respuesta principal
        final_reply = response
        if files_updated:
            final_reply += f"\n\n<i>📁 Guardado: {' · '.join(files_updated)}</i>"

        # Progreso de macros del día + recomendación (solo si se guardó comida y hay perfil)
        if alim_guardada and IS_CLOUD:
            perfil = load_perfil()
            if perfil.get("target_kcal"):
                final_reply += _macro_progress_msg(perfil)
                final_reply += _recomendacion_comida(perfil)

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


# ── Grupo familiar: gastos y tickets ─────────────────────────

def get_telegram_image_url(file_id: str) -> str:
    """Devuelve la URL pública de la imagen en Telegram (sin descargar)."""
    r = requests.get(
        f"{TELEGRAM_API}/getFile",
        params={"file_id": file_id},
        verify=SSL_VERIFY, timeout=10,
    )
    file_path = r.json()["result"]["file_path"]
    return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"


def download_telegram_image(file_id: str) -> bytes:
    """Descarga una imagen de Telegram y devuelve los bytes."""
    url = get_telegram_image_url(file_id)
    return requests.get(url, verify=SSL_VERIFY, timeout=30).content


def _resize_image(image_bytes: bytes, max_px: int = 1600) -> bytes:
    """Reduce la imagen a max_px en el lado más largo para optimizar el OCR."""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if max(w, h) > max_px:
            ratio = max_px / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return buf.getvalue()
    except Exception:
        return image_bytes  # si PIL no está disponible, usar original


def process_receipt_with_groq_vision(img_url: str) -> dict:
    """Envía la URL del ticket a Groq Vision y extrae total + items."""
    import re
    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)
    image_content = {"type": "image_url", "image_url": {"url": img_url}}

    # Paso 1: total + fecha
    chat_total = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[{"role": "user", "content": [
            image_content,
            {"type": "text", "text": (
                "Look at this receipt. Extract:\n"
                "1. TOTAL amount to pay (TOTAAL, TOTAL, Te betalen, SUMA, MONTANT)\n"
                "2. DATE of purchase (any date format on the receipt)\n"
                "Reply in exactly this format:\n"
                "TOTAL: 127.70\n"
                "DATE: 2026-05-13"
            )},
        ]}],
        max_tokens=40, temperature=0.0,
    )
    raw_total = chat_total.choices[0].message.content.strip()
    logger.info(f"Vision total+fecha: {raw_total!r}")
    m = re.search(r'\d+[.,]\d{2}', raw_total)
    total = float(m.group(0).replace(",", ".")) if m else 0.0

    # Extraer fecha del ticket
    from datetime import date as _date
    fecha_ticket = _date.today().isoformat()
    dm = re.search(r'DATE:\s*(\d{4}-\d{2}-\d{2})', raw_total)
    if not dm:
        # Intentar otros formatos: DD-MM-YYYY, DD/MM/YYYY
        dm2 = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', raw_total)
        if dm2:
            d, mo, y = dm2.group(1), dm2.group(2), dm2.group(3)
            fecha_ticket = f"{y}-{int(mo):02d}-{int(d):02d}"
    else:
        fecha_ticket = dm.group(1)
    logger.info(f"Fecha ticket: {fecha_ticket}")

    # Paso 2: lista de items
    items = []
    try:
        chat_items = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": [
                image_content,
                {"type": "text", "text": (
                    "List ALL purchased items from this receipt.\n"
                    "One per line: ORIGINAL_NAME | PRICE\n"
                    "Keep names in original language. Only lines with prices."
                )},
            ]}],
            max_tokens=1000, temperature=0.1,
        )
        raw_items = chat_items.choices[0].message.content.strip()
        logger.info(f"Vision items: {raw_items[:200]!r}")
        for line in raw_items.splitlines():
            if "|" not in line:
                continue
            parts = line.split("|")
            nombre = parts[0].strip().title()
            pm = re.search(r'\d+[.,]\d{2}', parts[-1])
            if nombre and pm:
                precio = float(pm.group(0).replace(",", "."))
                items.append({"nombre": nombre, "traduccion": "", "cantidad": 1,
                              "precio_unitario": precio, "total": precio})
    except Exception as e:
        logger.warning(f"Items no extraídos: {e}")

    # Paso 3: traducir items al español (llamada de texto, sin imagen)
    if items:
        try:
            nombres = [it["nombre"] for it in items]
            chat_trad = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": (
                    "Translate these product names to Spanish. "
                    "Reply with ONLY the translations, one per line, same order:\n"
                    + "\n".join(nombres)
                )}],
                max_tokens=500, temperature=0.1,
            )
            traducciones = chat_trad.choices[0].message.content.strip().splitlines()
            for i, trad in enumerate(traducciones[:len(items)]):
                items[i]["traduccion"] = trad.strip()
        except Exception as e:
            logger.warning(f"Traducción fallida: {e}")

    if total == 0.0 and not items:
        raise ValueError(f"No se pudo leer el ticket. Modelo respondió: {raw_total!r}")

    return {"total": total, "concepto": "Supermarkt", "fecha": fecha_ticket, "items": items}


def process_group_text_expense(text: str, sender_name: str) -> dict | None:
    """Usa Groq para extraer importe y concepto de un mensaje de texto."""
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        chat = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un extractor de gastos. Del mensaje del usuario, extrae el importe y concepto del gasto.\n"
                        "Devuelve SOLO un JSON válido: "
                        '{"importe": 0.0, "concepto": "descripcion", "categoria": "Comida"}\n'
                        "Categorías válidas: Comida, Limpieza, Higiene, Bebidas, Otros.\n"
                        "Si el mensaje no contiene un gasto claro, devuelve null.\n"
                        "Responde SOLO el JSON o null, sin texto adicional."
                    ),
                },
                {"role": "user", "content": text},
            ],
            max_tokens=128,
            temperature=0.1,
        )
        resp = chat.choices[0].message.content.strip()
        if resp.lower() == "null" or not resp:
            return None
        start = resp.find("{")
        end = resp.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(resp[start:end])
        return None
    except Exception as e:
        logger.error(f"Error extrayendo gasto de grupo: {e}")
        return None


def process_group_expense(msg: dict):
    """Procesa un mensaje del grupo familiar (texto o foto de ticket)."""
    chat_id = str(msg.get("chat", {}).get("id", ""))
    sender = msg.get("from", {})
    sender_name = sender.get("first_name") or sender.get("username") or "Familiar"
    text = msg.get("text", "").strip()
    lower = text.lower()

    # Comando /recomendar
    if lower in ("/recomendar", "/ahorro"):
        _send_family_savings_tip(chat_id)
        return

    # Comando /resumen
    if lower in ("/resumen", "/total"):
        _send_family_monthly_summary(chat_id)
        return

    # Comando /borrar — eliminar último ticket
    if lower == "/borrar":
        if IS_CLOUD:
            import supabase_client as sb
            borrado = sb.delete_ultimo_gasto_familia()
            if borrado:
                send_message(
                    f"🗑️ <b>Último registro borrado:</b>\n"
                    f"📅 {borrado.get('fecha')} · {borrado.get('concepto')} · €{borrado.get('importe', 0):.2f}\n\n"
                    f"<i>También se borraron los items de ese día. Ahora puedes volver a subir el ticket.</i>",
                    chat_id=chat_id,
                )
            else:
                send_message("No hay registros para borrar.", chat_id=chat_id)
        else:
            send_message("⚠️ Solo disponible en modo cloud.", chat_id=chat_id)
        return

    # Comando /borrar todo — eliminar todos los registros del mes
    if lower == "/borrar todo":
        if IS_CLOUD:
            import supabase_client as sb
            n = sb.delete_gastos_familia_mes()
            send_message(
                f"🗑️ <b>Mes limpiado.</b> Se borraron {n} registros y todos sus items.\n"
                f"Ahora puedes volver a subir los tickets con la fecha correcta.",
                chat_id=chat_id,
            )
        else:
            send_message("⚠️ Solo disponible en modo cloud.", chat_id=chat_id)
        return

    # Foto de ticket
    if msg.get("photo") or msg.get("document"):
        send_message("🧾 Analizando ticket...", chat_id=chat_id)
        try:
            photos = msg.get("photo", [])
            if photos:
                file_id = photos[-1]["file_id"]
            else:
                file_id = msg["document"]["file_id"]
            img_url = get_telegram_image_url(file_id)
            receipt = process_receipt_with_groq_vision(img_url)

            total = float(receipt.get("total") or 0)
            concepto = receipt.get("concepto") or "Supermercado"
            items = receipt.get("items") or []
            fecha_ticket = receipt.get("fecha") or datetime.now().date().isoformat()

            if IS_CLOUD:
                import supabase_client as sb
                sb.insert_gasto_familia(sender_name, concepto, total, "Comida", "foto", fecha=fecha_ticket)
                if items:
                    sb.insert_items_compra(items, fecha=fecha_ticket)
                resumen_mes = sb.get_gastos_familia_mes()
                total_mes = resumen_mes.get("total", 0)
            else:
                total_mes = total

            items_text = ""
            for it in items[:8]:
                nom = it.get("nombre") or it.get("item_nombre") or "—"
                trad = it.get("traduccion", "")
                tot = it.get("total") or ""
                etiqueta = f"{nom} ({trad})" if trad else nom
                items_text += f"  • {etiqueta}" + (f" — €{tot}" if tot else "") + "\n"

            send_message(
                f"✅ <b>Ticket registrado</b> — {sender_name}\n"
                f"📅 Fecha: <b>{fecha_ticket}</b>\n"
                f"💶 Total: <b>€{total:.2f}</b>\n\n"
                f"<b>Items:</b>\n{items_text or '  (no detectados)'}\n"
                f"<i>💰 Total mes: €{total_mes:.2f}</i>",
                chat_id=chat_id,
            )
        except Exception as e:
            logger.error(f"Error procesando ticket: {e}", exc_info=True)
            send_message(
                f"❌ <b>Error leyendo ticket:</b>\n<code>{type(e).__name__}: {str(e)[:300]}</code>\n\n"
                f"Envía el importe manualmente: <code>lidl 127.70</code>",
                chat_id=chat_id,
            )
        return

    # Mensaje de texto con gasto
    if text:
        gasto = process_group_text_expense(text, sender_name)
        if gasto and gasto.get("importe"):
            importe = float(gasto["importe"])
            concepto = gasto.get("concepto", "Compra")
            categoria = gasto.get("categoria", "Comida")

            if IS_CLOUD:
                import supabase_client as sb
                sb.insert_gasto_familia(sender_name, concepto, importe, categoria, "texto")
                resumen_mes = sb.get_gastos_familia_mes()
                total_mes = resumen_mes.get("total", 0)
            else:
                total_mes = importe

            send_message(
                f"✅ <b>Gasto registrado</b> — {sender_name}\n"
                f"💶 {concepto}: <b>€{importe:.2f}</b>\n"
                f"<i>Total mes: €{total_mes:.2f}</i>",
                chat_id=chat_id,
            )


def _send_family_monthly_summary(chat_id: str):
    """Envía resumen mensual de gastos familiares al grupo."""
    if not IS_CLOUD:
        send_message("⚠️ Resumen disponible solo en modo cloud.", chat_id=chat_id)
        return
    import supabase_client as sb
    datos = sb.get_gastos_familia_mes()
    total = datos.get("total", 0)
    por_cat = datos.get("por_categoria", {})
    registros = datos.get("registros", [])

    cat_text = "\n".join(f"  • {cat}: €{imp:.2f}" for cat, imp in sorted(por_cat.items(), key=lambda x: -x[1]))
    top_items = sb.get_top_items_mes(5)
    items_text = "\n".join(f"  {i+1}. {it['item']}: €{it['total']:.2f}" for i, it in enumerate(top_items))

    send_message(
        f"📊 <b>RESUMEN FAMILIAR — {datetime.now().strftime('%B %Y').upper()}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💶 <b>Total gastado: €{total:.2f}</b>\n\n"
        f"<b>Por categoría:</b>\n{cat_text or '  (sin datos)'}\n\n"
        f"<b>Top items más comprados:</b>\n{items_text or '  (sin datos)'}",
        chat_id=chat_id,
    )


def _send_family_savings_tip(chat_id: str):
    """Genera recomendaciones de ahorro con Groq y las envía al grupo."""
    send_message("💡 Analizando vuestros gastos...", chat_id=chat_id)
    if not IS_CLOUD:
        send_message("⚠️ Disponible solo en modo cloud.", chat_id=chat_id)
        return
    import supabase_client as sb
    datos = sb.get_gastos_familia_mes()
    top_items = sb.get_top_items_mes(10)
    top_str = [it["item"] + ":€" + f"{it['total']:.2f}" for it in top_items]
    resumen = (
        f"Gastos familiares del mes: €{datos['total']:.2f}\n"
        f"Desglose: {datos['por_categoria']}\n"
        f"Top items: {top_str}"
    )
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        chat = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un asesor financiero familiar. Analiza los datos de gasto "
                        "y da 3-4 recomendaciones concretas y prácticas para ahorrar en la compra. "
                        "Responde en español con formato HTML de Telegram (usa <b> y <i>). "
                        "Sé específico con los items más caros."
                    ),
                },
                {"role": "user", "content": resumen},
            ],
            max_tokens=512,
            temperature=0.5,
        )
        tip = chat.choices[0].message.content.strip()
        send_message(f"💡 <b>RECOMENDACIONES DE AHORRO</b>\n\n{tip}", chat_id=chat_id)
    except Exception as e:
        logger.error(f"Error generando tips de ahorro: {e}")
        send_message("❌ No pude generar las recomendaciones ahora.", chat_id=chat_id)


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
_processed_msg_ids: set = set()

def _make_flask_app():
    import threading
    from flask import Flask, request, jsonify
    flask_app = Flask(__name__)

    @flask_app.route("/webhook", methods=["POST"])
    def webhook():
        data   = request.get_json(force=True, silent=True) or {}
        msg    = data.get("message", {})
        msg_id = msg.get("message_id")

        # Deduplicar: Telegram reenvía si no recibe 200 a tiempo
        if msg_id:
            if msg_id in _processed_msg_ids:
                return jsonify({"ok": True})
            _processed_msg_ids.add(msg_id)
            if len(_processed_msg_ids) > 500:
                _processed_msg_ids.clear()

        incoming_chat_id = str(msg.get("chat", {}).get("id", ""))

        # Grupo familiar
        if GRUPO_FAMILIA_CHAT_ID and incoming_chat_id == GRUPO_FAMILIA_CHAT_ID:
            def handle_group():
                process_group_expense(msg)
            threading.Thread(target=handle_group, daemon=True).start()
            return jsonify({"ok": True})

        if incoming_chat_id != CHAT_ID:
            return jsonify({"ok": True})

        # Procesar TODO en background para devolver 200 inmediatamente
        def handle():
            text = msg.get("text", "").strip()
            if not text and msg.get("voice"):
                send_message("🎤 Transcribiendo tu audio...")
                text = transcribe_voice(msg["voice"]["file_id"])
                if not text:
                    send_message("❌ No pude transcribir el audio. Intenta enviarlo como texto.")
                    return
            if text:
                process_message(text)

        threading.Thread(target=handle, daemon=True).start()
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
                            msg = update.get("message", {})
                            incoming = str(msg.get("chat", {}).get("id", ""))
                            if GRUPO_FAMILIA_CHAT_ID and incoming == GRUPO_FAMILIA_CHAT_ID:
                                process_group_expense(msg)
                            elif incoming == CHAT_ID:
                                text = msg.get("text", "").strip()
                                if text:
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

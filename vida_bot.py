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
    increment_counter, check_achievements, get_streak_multiplier,
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
CREATINE_TIME   = os.environ.get("CREATINE_TIME")   or cfg.get("vida_bot", {}).get("creatine_time", "09:00")
DATO_MAÑANA     = os.environ.get("DATO_MAÑANA")     or cfg.get("vida_bot", {}).get("dato_manana", "08:30")
DATO_TARDE      = os.environ.get("DATO_TARDE")      or cfg.get("vida_bot", {}).get("dato_tarde",  "14:00")
DATO_NOCHE      = os.environ.get("DATO_NOCHE")      or cfg.get("vida_bot", {}).get("dato_noche",  "20:00")

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
_last_recommended_mejora: dict = {}
_noche_counter = 0           # cada 3 noches → idea de negocio en lugar de dato curioso
_DATOS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos_log.json")

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
_ikigai_conv: dict = {}

IKIGAI_STEPS = [
    {"circulo": "amas",    "pregunta": "🌸 <b>Pregunta 1/10 — Lo que AMAS</b>\n\n¿Qué actividades te hacen olvidar el tiempo cuando las haces?"},
    {"circulo": "amas",    "pregunta": "🌸 <b>Pregunta 2/10 — Lo que AMAS</b>\n\n¿De qué temas podrías hablar durante horas sin cansarte?"},
    {"circulo": "amas",    "pregunta": "🌸 <b>Pregunta 3/10 — Lo que AMAS</b>\n\n¿Qué harías con tu tiempo si el dinero no importara en absoluto?"},
    {"circulo": "bien",    "pregunta": "💪 <b>Pregunta 4/10 — En lo que ERES BUENO</b>\n\n¿En qué te dice la gente que destacas o que eres especialmente bueno?"},
    {"circulo": "bien",    "pregunta": "💪 <b>Pregunta 5/10 — En lo que ERES BUENO</b>\n\n¿Qué habilidades o conocimientos tienes que otros no suelen tener?"},
    {"circulo": "mundo",   "pregunta": "🌍 <b>Pregunta 6/10 — Lo que el MUNDO NECESITA</b>\n\n¿Qué problemas del mundo o de tu entorno te indignan y querrías resolver?"},
    {"circulo": "mundo",   "pregunta": "🌍 <b>Pregunta 7/10 — Lo que el MUNDO NECESITA</b>\n\n¿Cómo crees que podrías ayudar a otros con lo que sabes o amas?"},
    {"circulo": "pagan",   "pregunta": "💰 <b>Pregunta 8/10 — Por lo que te PAGAN</b>\n\n¿Por qué cosas tienes conocimiento o habilidades que alguien pagaría?"},
    {"circulo": "pagan",   "pregunta": "💰 <b>Pregunta 9/10 — Por lo que te PAGAN</b>\n\n¿Tienes experiencias o habilidades que ya están demandadas en el mercado?"},
    {"circulo": "profundo","pregunta": "🔮 <b>Pregunta 10/10 — Lo más Profundo</b>\n\n¿Cuál es tu mayor miedo en la vida y cuál es tu mayor sueño?\nSé tan honesto como puedas."},
]

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


_PORCIONES = [
    # (nombre_display, prot, carbs, kcal, tiene_lactosa)
    ("Pechuga de pollo (200g)",    62,  0,  220, False),
    ("Pechuga de pollo (150g)",    47,  0,  165, False),
    ("Atún en lata (150g)",        35,  0,  165, False),
    ("Huevos revueltos (3 uds)",   19,  1,  215, False),
    ("Queso cottage (200g)",       26,  6,  170,  True),
    ("Yogur griego 0% (200g)",     20,  8,  120,  True),
    ("Arroz cocido (200g)",         5, 46,  215, False),
    ("Arroz cocido (150g)",         4, 35,  165, False),
    ("Avena cocida (80g seco)",    10, 52,  285, False),
    ("Plátano (2 piezas)",          3, 54,  230, False),
    ("Tostadas integrales (2 uds)", 4, 28,  150, False),
    ("Almendras (30g)",             6,  5,  175, False),
]


def _recomendacion_comida(perfil: dict) -> str:
    """Genera sugerencias de comida con porciones realistas para completar los targets del día."""
    hoy = get_macros_hoy()
    prot_falta  = max(perfil.get("target_prot",  150) - hoy["prot_g"],   0)
    kcal_falta  = max(perfil.get("target_kcal", 2800) - hoy["kcal"],     0)
    carbs_falta = max(perfil.get("target_carbs", 350) - hoy["carbs_g"],  0)
    intol = (perfil.get("intolerancias") or "ninguna").lower()

    if prot_falta < 10 and kcal_falta < 100:
        return "\n\n✅ <b>¡Targets del día alcanzados! Buen trabajo.</b>"

    sugerencias = []
    prot_cubierta  = 0
    kcal_cubierta  = 0
    carbs_cubiertos = 0

    for nombre, prot, carbs, kcal, es_lactosa in _PORCIONES:
        if len(sugerencias) >= 3:
            break
        if es_lactosa and "lactosa" in intol:
            continue
        util = False
        if prot_falta > 15 and prot >= 15 and prot_cubierta < prot_falta:
            util = True
        if kcal_falta > 150 and carbs >= 20 and carbs_cubiertos < carbs_falta and carbs_falta > 30:
            util = True
        if util:
            sugerencias.append(f"• {nombre} → +{prot}g prot, +{carbs}g carbos, +{kcal} kcal")
            prot_cubierta  += prot
            kcal_cubierta  += kcal
            carbs_cubiertos += carbs

    if not sugerencias:
        if kcal_falta > 0:
            sugerencias.append(f"• Un snack de ~{round(kcal_falta)}kcal (fruta + frutos secos)")
        else:
            return "\n\n✅ <b>¡Targets del día alcanzados! Buen trabajo.</b>"

    distribuir = ""
    if prot_falta > 80 or kcal_falta > 900:
        distribuir = "\n  <i>↳ Distribúyelo en 2 o más comidas</i>"

    recs = "\n".join(sugerencias)
    return f"\n\n🍽️ <b>Para completar el día:</b>\n{recs}{distribuir}"


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
BASE_SYSTEM_PROMPT = """Eres el equipo de expertos personales del usuario. Tu rol es DUAL:
1. EXTRACTOR: Extraes información estructurada del mensaje del usuario
2. EXPERTO: Cada especialista responde solo si hay contenido relevante para su área

EQUIPO DE EXPERTOS (solo responden si el mensaje toca su área):
- 🥗 Marcos — Nutricionista Deportivo: Si el usuario menciona comida o bebida, estima los macros de ESA comida concreta (kcal, proteína, carbos, grasas) con los valores más representativos. Si no hay cantidades exactas, estima porciones normales. Rellena "kcal", "prot", "carbs", "grasas" en el JSON con números. Usa el perfil y el acumulado del día para dar consejos específicos. NUNCA sugiere cantidades irreales (no más de 300g cocido de arroz, no más de 250g de pollo en una sugerencia). Si le falta mucho al target, indica distribuir en varias comidas.
- 💪 Hugo — Entrenador Personal: Solo si el usuario menciona entrenamiento, ejercicio o actividad física. Usa sus días de entreno y objetivo para sugerir progresiones o recuperación. Si el usuario dicta un ejercicio de gym con reps/peso, confirma la serie y sugiere el peso o reps para la siguiente serie (progresión).
- 🙏 Padre Alberto — Guía Espiritual: Solo si el usuario menciona a Dios, fe, oración, gratitud espiritual o prácticas religiosas.
- 💰 Ricardo — Asesor Financiero: Solo si el usuario menciona gastos, compras, pagos o ingresos.
- 🚀 Sara — Business Consultant: Solo si el usuario menciona ideas de negocio o proyectos.
- 📊 Analista de Trading: Solo si el usuario menciona trading, mercados o señales.

FORMATO DE RESPUESTA: Devuelve SIEMPRE un JSON válido con EXACTAMENTE esta estructura:
{
  "updates": {
    "deporte": {"actividad": "", "duracion": "", "distancia": "", "sensacion": "😊", "notas": ""},
    "gym": [{"ejercicio": "", "reps": 0, "peso": 0}],
    "alimentacion": {"desayuno": "", "comida": "", "cena": "", "snacks": "", "kcal": "", "prot": "", "carbs": "", "grasas": "", "agua": "", "energia": "😊"},
    "lexico": [{"palabra": "", "definicion": "", "ejemplo": ""}],
    "refranes": [{"refran": "", "significado": "", "contexto": ""}],
    "gastos": [{"categoria": "", "concepto": "", "importe": 0.0}],
    "ingresos": [{"categoria": "", "concepto": "", "importe": 0.0}],
    "ideas": [{"idea": "", "inversion": "", "tiempo": "", "potencial": ""}],
    "trading": {"observacion": "", "tipo": "💡", "accion": ""},
    "habitos": {"ducha_fria": false, "te_clavo": false, "oracion": false, "silencio": false, "creatina": false},
    "diario": {"lo_importante": "", "gratitud": "", "mejora": "", "habitos_ok": ""}
  },
  "responses": {
    "nutricionista": "Solo si hay comida/nutrición: 2-3 oraciones en HTML Telegram. Habla usando el perfil real del usuario (peso, tipo de cuerpo, lo que ya comió hoy). Tono cercano y directo, como un amigo experto. Deja en blanco si el mensaje no habla de comida.",
    "entrenador": "",
    "espiritual": "",
    "asesor": "",
    "consultor": "",
    "analista": ""
  }
}

REGLAS IMPORTANTES:
- Solo incluye campos con datos reales (deja "" los que no apliquen, false los booleans sin datos)
- "gym": SOLO cuando el usuario dicta un EJERCICIO CONCRETO de gimnasio (ej: "curl de bíceps 12 reps con 10kg", "press banca 8 reps 40 kilos", "sentadilla 10 repeticiones"). Un item por cada ejercicio mencionado.
  · "ejercicio": nombre estándar en minúsculas y singular (ej: "curl de bíceps", "press de banca", "sentadilla", "peso muerto", "remo con barra", "press militar", "dominadas", "fondos")
  · "reps": número entero de repeticiones (0 si no lo dice)
  · "peso": número en kg (0 si es peso corporal o no lo dice)
  · NO rellenes "deporte" cuando uses "gym" — el sistema registra el día de gym automáticamente
  · El sistema cuenta las series solo: si el usuario repite el mismo ejercicio el mismo día, se guarda como la siguiente serie
- "deporte": para actividades generales (correr, bici, fútbol, caminar...) o cuando dice "fui al gym" SIN dictar ejercicios concretos
- En "responses": deja "" en los expertos que no tienen contenido relevante en el mensaje
- Cada experto habla en primera persona, conoce al usuario por su perfil, menciona datos reales cuando aplica
- Categorías de GASTO válidas: Comida, Gasolina, Transporte, Ocio, Ropa, Salud, Formación, Ahorro, Hogar, Servicios, Suscripciones, Extra
- Categorías de INGRESO válidas: Nómina, Freelance, Trading, Extra, Otros
- "gastos" = dinero que SALE. "ingresos" = dinero que ENTRA
- Si el usuario menciona que cobró, recibió dinero, nómina, sueldo → usa "ingresos", NO "gastos"
- Cuando haya datos de alimentación, SIEMPRE rellena kcal/prot/carbs/grasas con estimación numérica
- Usa <b>negrita</b> e <i>cursiva</i> en los mensajes HTML
- Responde siempre en ESPAÑOL"""


def build_system_prompt() -> str:
    """Construye el system prompt con el perfil completo del usuario y macros acumulados del día."""
    perfil = load_perfil()
    macros_hoy = get_macros_hoy()
    if perfil.get("target_kcal"):
        contexto_personal = (
            f"PERFIL DEL USUARIO:\n"
            f"  Peso: {perfil.get('peso','?')}kg · Altura: {perfil.get('altura','?')}cm · "
            f"Edad: {perfil.get('edad','?')} años\n"
            f"  Tipo de cuerpo: {perfil.get('tipo_cuerpo','').capitalize()} · "
            f"Entrena: {perfil.get('dias_entreno','?')} días/semana\n"
            f"  Intolerancias: {perfil.get('intolerancias','ninguna')}\n"
            f"OBJETIVO: Ganar masa muscular\n"
            f"TARGETS DIARIOS: {perfil['target_kcal']} kcal · {perfil['target_prot']}g prot · "
            f"{perfil['target_carbs']}g carbos · {perfil['target_grasas']}g grasas · 3L agua\n"
            f"HOY ACUMULADO (antes de este mensaje): "
            f"{macros_hoy['kcal']:.0f} kcal · {macros_hoy['prot_g']:.0f}g prot · "
            f"{macros_hoy['carbs_g']:.0f}g carbos · {macros_hoy['grasas_g']:.0f}g grasas"
        )
    else:
        contexto_personal = (
            "PERFIL DEL USUARIO: No configurado aún\n"
            "OBJETIVO: Ganar masa muscular (targets aprox: 2800kcal · 150g prot · 350g carbos · 78g grasas)"
        )
    return contexto_personal + "\n\n" + BASE_SYSTEM_PROMPT


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
            max_tokens=1500,
            temperature=0.6,
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

    # ── Ejercicios de gym: cada mención = 1 serie (auto-incremento) ──
    gym_sets = updates.get("gym", []) or []
    if isinstance(gym_sets, dict):
        gym_sets = [gym_sets]
    gym_guardado = False
    for g in gym_sets:
        if not isinstance(g, dict):
            continue
        ejercicio = str(g.get("ejercicio", "")).strip()
        if not ejercicio:
            continue
        serie = sb.insert_gym_set(ejercicio, g.get("reps"), g.get("peso")) if IS_CLOUD else 0
        if serie:
            gym_guardado = True
            detalle = f"Serie {serie}"
            try:
                reps = int(float(g.get("reps") or 0))
                if reps > 0:
                    detalle += f" · {reps} reps"
            except (ValueError, TypeError):
                pass
            try:
                peso = float(str(g.get("peso") or 0).lower().replace("kg", "").replace(",", ".").strip() or 0)
                if peso > 0:
                    detalle += f" × {peso:g} kg"
            except (ValueError, TypeError):
                pass
            files_updated.append(f"🏋️ {ejercicio.title()} ({detalle})")

    # El primer ejercicio del día crea la entrada "Gym" en deporte (1 vez/día)
    if gym_guardado and IS_CLOUD and not sb.deporte_hoy_tiene("gym"):
        sb.insert_deporte("Gym", "", "", "💪", "Día de gym (auto-registrado al dictar ejercicios)")
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

    for ingreso in updates.get("ingresos", []):
        if ingreso.get("concepto") and ingreso.get("importe"):
            if IS_CLOUD:
                sb.insert_ingreso(ingreso.get("categoria", "Otros"), ingreso["concepto"], float(ingreso["importe"]))
            files_updated.append(f"💰 Ingreso €{ingreso['importe']}")

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

    def _award_habit(action_key: str, streak_key: str):
        nonlocal xp_state
        streak = xp_state.get("streaks", {}).get(streak_key, 0)
        result = award_xp(action_key, xp_state, streak_days=streak)
        xp_messages.extend(result["messages"])
        xp_state = result["state"]
        update_streak(streak_key, True, xp_state)
        xp_state = load_state()

    if habitos.get("ducha_fria"):
        _award_habit("ducha_fria", "ducha_fria")
    else:
        all_habits_done = False

    if habitos.get("te_clavo"):
        _award_habit("te_clavo", "te_clavo")
    else:
        all_habits_done = False

    if habitos.get("oracion"):
        _award_habit("oracion_cumplida", "oracion")
    else:
        all_habits_done = False

    if habitos.get("silencio"):
        _award_habit("silencio_cumplido", "silencio")

    if habitos.get("creatina"):
        _award_habit("creatina_cumplida", "creatina")

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
            habitos.get("creatina", False),
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

    # Si hay un flujo de ikigai activo, capturar la respuesta
    if _ikigai_conv and text.strip().lower() not in ("/ikigai",):
        if _handle_ikigai_step(text):
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
            "• Hábitos ✅ · Creatina 💊 · Ideas 💡 · Reflexiones ✝️🙏\n\n"
            "<b>Comandos:</b>\n"
            "/perfil — Tu ficha de personaje y XP\n"
            "/logros — Ver logros desbloqueados\n"
            "/mejoras — Ver mejoras en proceso\n"
            "/recomendar — Recibir nueva mejora de la semana\n"
            "/reto — Retar a tus amigos en el grupo\n"
            "/estado — Estado del sistema de vida\n"
            "/datos — Recibir dato curioso ahora\n"
            "/datos [categoria] — Ej: /datos negocios\n"
            "/ikigai — Descubrir tu propósito de vida\n\n"
            "<b>Atajos:</b>\n"
            "<code>curl de bíceps 12 reps con 10kg</code> — Registra serie de gym (repítelo y cuenta Serie 2, 3...)\n"
            "<code>creatina ✅</code> — Registrar creatina del día\n"
            "<code>quiero intentarlo</code> — Añadir mejora recomendada a tus hábitos\n"
            "<code>? tu pregunta</code> — Modo chat libre (sin guardar datos)"
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

    if lower in ["/mejoras", "/mejora"]:
        if not IS_CLOUD:
            send_message("⚠️ /mejoras requiere modo cloud.")
            return
        from supabase_client import get_mejoras
        activas   = get_mejoras("activa")
        en_proceso = get_mejoras("en_proceso")
        xp_state  = load_state()
        streaks   = xp_state.get("streaks", {})
        lines = ["💡 <b>TUS MEJORAS</b>\n" + "─" * 22]
        if en_proceso:
            lines.append("\n🔄 <b>En proceso:</b>")
            for m in en_proceso:
                dias = (datetime.now().date() - __import__("datetime").date.fromisoformat(m["fecha_inicio"])).days if m.get("fecha_inicio") else 0
                lines.append(f"  • {m['nombre']} <i>({dias}d)</i>")
        if activas:
            lines.append("\n✅ <b>Activas (consolidadas):</b>")
            for m in activas:
                lines.append(f"  • {m['nombre']}")
        if not en_proceso and not activas:
            lines.append("\n<i>No tienes mejoras en proceso todavía.</i>")
            lines.append("Escribe /recomendar para recibir una.")
        lines.append(f"\n💊 Racha creatina: <b>{streaks.get('creatina', 0)} días</b>")
        send_message("\n".join(lines))
        return

    if lower in ["/recomendar", "/mejora nueva"]:
        send_mejora_semanal()
        return

    if lower == "/ikigai":
        _ikigai_conv.clear()
        _ikigai_conv["step"] = 0
        _ikigai_conv["answers"] = {}
        sep = "─" * 24
        msg = (
            "🌸 <b>DESCUBRE TU IKIGAI</b>\n" + sep + "\n\n"
            "El <i>ikigai</i> (生き甲斐) es el concepto japonés del \"razón de ser\".\n"
            "Te haré <b>10 preguntas</b>. Responde con total honestidad.\n\n"
            "No hay respuestas correctas ni incorrectas — solo las tuyas.\n\n"
        ) + IKIGAI_STEPS[0]["pregunta"]
        send_message(msg)
        return

    if lower.startswith("/datos"):
        parts = text.strip().split(maxsplit=1)
        cat = parts[1].strip() if len(parts) > 1 else None
        send_dato_curioso(slot="mañana", categoria_override=cat)
        return

    # Detectar "quiero intentarlo"
    _INTENTAR = ["quiero intentarlo", "quiero intentar", "me apunto", "voy a intentar", "lo voy a intentar"]
    if any(p in lower for p in _INTENTAR):
        handle_intentar_mejora(text)
        return

    # Detectar pregunta directa (modo chat libre sin extracción de datos)
    _CHAT_PREFIXES = ("? ", "pregunta:", "/pregunta ", "dime ", "explícame ", "explicame ", "qué es ", "que es ")
    if lower.startswith(_CHAT_PREFIXES) or lower.startswith("?"):
        question = text.lstrip("?").strip()
        if question:
            handle_chat_question(question)
            return

    # Detectar registro de creatina directo (sin necesitar al AI)
    _CREATINA_KEYS = ["creatina ✅", "creatina ok", "tomé creatina", "tome creatina", "creatina tomada", "creatina sí", "creatina si"]
    if any(k in lower for k in _CREATINA_KEYS):
        xp_state = load_state()
        racha = xp_state.get("streaks", {}).get("creatina", 0)
        result = award_xp("creatina_cumplida", xp_state, streak_days=racha)
        update_streak("creatina", True, result["state"])
        xp_state = load_state()
        if IS_CLOUD:
            from supabase_client import sync_xp_to_supabase
            sync_xp_to_supabase(os.path.join(SCRIPT_DIR, "vida_xp.json"))
        send_message(
            f"💊 <b>Creatina registrada</b> ✅\n"
            f"{''.join(result['messages'])}\n"
            f"🔥 Racha: <b>{xp_state['streaks'].get('creatina', 0)} días</b>"
        )
        return

    # Procesar mensaje libre con IA
    send_message("⏳ Analizando tu día...")

    _EXPERT_LABELS = {
        "nutricionista": "🥗 Marcos · Nutricionista Deportivo",
        "entrenador":    "💪 Hugo · Entrenador Personal",
        "espiritual":    "🙏 Padre Alberto · Guía Espiritual",
        "asesor":        "💰 Ricardo · Asesor Financiero",
        "consultor":     "🚀 Sara · Business Consultant",
        "analista":      "📊 Carlos · Analista de Trading",
    }

    try:
        vida_state = read_vida_state()
        result     = call_groq(text, vida_state)
        updates    = result.get("updates", {})

        files_updated, xp_messages, alim_guardada = apply_updates(updates)
        last_entry_date = datetime.now().date()

        # Confirmación de lo guardado (mensaje breve)
        if files_updated:
            send_message(f"<i>📁 Guardado: {' · '.join(files_updated)}</i>")

        # Obtener respuestas por experto (con fallback al formato antiguo)
        expert_responses = result.get("responses", {})
        if not expert_responses and result.get("response"):
            expert_responses = {"nutricionista": result["response"]}

        # Enviar un mensaje separado por cada experto con contenido
        perfil_cache = None
        for key, label in _EXPERT_LABELS.items():
            msg = expert_responses.get(key, "")
            if not isinstance(msg, str):
                msg = ""
            msg = msg.strip()
            if not msg:
                continue

            expert_msg = f"<b>{label}</b>\n{'─' * 22}\n{msg}"

            # Añadir barras de progreso macros + sugerencias al mensaje del nutricionista
            if key == "nutricionista" and alim_guardada and IS_CLOUD:
                if perfil_cache is None:
                    perfil_cache = load_perfil()
                if perfil_cache.get("target_kcal"):
                    expert_msg += _macro_progress_msg(perfil_cache)
                    expert_msg += _recomendacion_comida(perfil_cache)

            send_message(expert_msg)

        # Si ningún experto respondió y tampoco hubo guardado, enviar acuse mínimo
        if not any(expert_responses.get(k, "").strip() for k in _EXPERT_LABELS) and not files_updated:
            send_message("✅ Recibido.")

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


# ── Datos curiosos diarios ─────────────────────────────────────
_SLOT_CATS = {
    "mañana": ["ciencia", "cuerpo humano", "psicología", "física", "biología"],
    "tarde":  ["historia", "tecnología", "naturaleza", "geografía", "astronomía"],
    "noche":  ["negocios", "economía", "emprendimiento", "psicología del dinero", "productividad"],
}


def _get_titulos_recientes() -> list:
    """Lee los últimos 30 títulos enviados (Supabase en cloud, JSON local en modo local)."""
    if IS_CLOUD:
        try:
            from supabase_client import get_temas_recientes
            return get_temas_recientes(30)
        except Exception:
            pass
    try:
        with open(_DATOS_LOG, "r", encoding="utf-8") as f:
            return json.load(f).get("titulos", [])[-30:]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _guardar_titulo(titulo: str, categoria: str, slot: str):
    if IS_CLOUD:
        try:
            from supabase_client import insert_dato_enviado
            insert_dato_enviado(titulo, categoria, slot)
        except Exception:
            pass
    # Siempre guarda local también como backup
    try:
        data = {}
        try:
            with open(_DATOS_LOG, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        titulos = data.get("titulos", [])
        titulos.append(titulo)
        data["titulos"] = titulos[-60:]   # máximo 60 en local
        with open(_DATOS_LOG, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"_guardar_titulo local: {e}")


def send_dato_curioso(slot: str = "mañana", categoria_override: str = None):
    global _noche_counter
    import random as _random

    # Cada 3ª noche → idea de negocio
    if slot == "noche":
        _noche_counter += 1
        if _noche_counter % 3 == 0:
            _send_idea_negocio_diaria()
            return

    cats = _SLOT_CATS.get(slot, _SLOT_CATS["mañana"])
    categoria = categoria_override or _random.choice(cats)
    recientes = _get_titulos_recientes()
    recientes_str = ", ".join(f'"{t}"' for t in recientes[-20:]) if recientes else "ninguno"

    prompt = (
        f"Genera UN dato curioso interesante de la categoría: {categoria}.\n"
        f"El título NO debe ser igual ni muy similar a ninguno de estos ya enviados: {recientes_str}.\n\n"
        f"Devuelve SOLO un JSON válido con esta estructura exacta:\n"
        f'{{"titulo": "Título llamativo máx 8 palabras", '
        f'"contenido": "Explicación clara en 3-4 frases con números o ejemplos concretos.", '
        f'"por_que": "1-2 frases sobre el mecanismo o causa profunda.", '
        f'"categoria": "{categoria}"}}'
    )
    try:
        from groq import Groq
        import re as _re
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=350,
            temperature=0.9,
        )
        raw = resp.choices[0].message.content.strip()
        match = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if not match:
            logger.error(f"send_dato_curioso: no JSON en respuesta")
            return
        dato = json.loads(match.group())
        titulo    = dato.get("titulo", "")
        contenido = dato.get("contenido", "")
        por_que   = dato.get("por_que", "")
        cat_out   = dato.get("categoria", categoria)

        _guardar_titulo(titulo, cat_out, slot)

        send_message(
            f"💡 <b>DATO CURIOSO</b>\n"
            f"{'─' * 22}\n"
            f"<b>{titulo.upper()}</b>\n\n"
            f"{contenido}\n\n"
            f"🔬 <i>¿Por qué es así?</i>\n"
            f"{por_que}"
        )
    except Exception as e:
        logger.error(f"send_dato_curioso: {e}")


def _send_idea_negocio_diaria():
    """Genera una idea de negocio con IA y la envía como mensaje nocturno."""
    xp_state = load_state()
    existing = []
    if IS_CLOUD:
        try:
            from supabase_client import get_mejoras
            existing = [m["nombre"] for m in get_mejoras()]
        except Exception:
            pass
    recientes_str = ", ".join(existing[-10:]) if existing else "ninguna"

    prompt = (
        f"Genera UNA idea de negocio innovadora para un hombre joven con habilidades en tecnología, trading y automatización.\n"
        f"Ideas recientes ya enviadas (no repetir): {recientes_str}.\n\n"
        f"Devuelve SOLO un JSON:\n"
        f'{{"nombre": "Nombre de la idea", '
        f'"descripcion": "Descripción en 2-3 frases de qué es y cómo funciona.", '
        f'"inversion": "Estimación de inversión inicial", '
        f'"tiempo": "Tiempo estimado para primeros ingresos", '
        f'"potencial": "Potencial de ingresos mensuales estimado"}}'
    )
    try:
        from groq import Groq
        import re as _re
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.9,
        )
        raw = resp.choices[0].message.content.strip()
        match = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if not match:
            return
        idea = json.loads(match.group())
        send_message(
            f"🚀 <b>IDEA DE NEGOCIO</b>\n"
            f"{'─' * 22}\n"
            f"<b>{idea.get('nombre','').upper()}</b>\n\n"
            f"{idea.get('descripcion','')}\n\n"
            f"💰 Inversión: {idea.get('inversion','?')} · "
            f"⏱️ {idea.get('tiempo','?')}\n"
            f"🎯 Potencial: {idea.get('potencial','?')}"
        )
    except Exception as e:
        logger.error(f"_send_idea_negocio_diaria: {e}")


# ── Recordatorio creatina ──────────────────────────────────────
def send_creatine_reminder():
    xp_state = load_state()
    racha = xp_state.get("streaks", {}).get("creatina", 0)
    mult  = get_streak_multiplier(racha)
    mult_txt = f" · multiplicador XP: ×{mult:.2f}" if mult > 1.0 else ""
    send_message(
        f"💊 <b>¡Hora de la creatina!</b>\n"
        f"Toma 5g de creatina monohidrato con el desayuno.\n"
        f"🔥 Racha: <b>{racha} días</b>{mult_txt}\n\n"
        f"Responde <code>creatina ✅</code> para ganar XP y sumar a tu racha."
    )


# ── Mejoras: recomendación generada por IA ─────────────────────
def _generate_mejora_ai() -> dict:
    """Pide a Groq una nueva mejora personalizada basada en el perfil y lo que ya tiene."""
    xp_state = load_state()
    streaks  = xp_state.get("streaks", {})

    existing_names = []
    if IS_CLOUD:
        try:
            from supabase_client import get_mejoras
            existing_names = [m["nombre"] for m in get_mejoras()]
        except Exception:
            pass

    existing_str = ", ".join(existing_names) if existing_names else "ninguna todavía"
    ducha_racha   = streaks.get("ducha_fria", 0)
    creatina_racha = streaks.get("creatina", 0)

    prompt = (
        f"El usuario es un hombre joven con objetivo de ganar masa muscular.\n"
        f"Hábitos actuales: ducha fría ({ducha_racha} días de racha), "
        f"té de clavo, oración diaria, creatina ({creatina_racha} días).\n"
        f"Mejoras que ya tiene registradas: {existing_str}.\n\n"
        f"Genera UNA nueva mejora de bienestar que NO esté en esa lista. "
        f"Puede ser de cualquier área: testosterona, recuperación, sueño, estrés, "
        f"enfoque, nutrición, salud hormonal, bienestar mental, rendimiento físico...\n\n"
        f"Devuelve SOLO un JSON válido con esta estructura exacta:\n"
        f'{{"nombre": "...", "categoria": "testosterona|estres|recuperacion|sueno|enfoque", '
        f'"descripcion": "instrucción práctica en 1-2 frases", '
        f'"evidencia": "explicación científica con datos reales en 2-3 frases"}}'
    )

    try:
        from groq import Groq
        import re as _re
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.85,
        )
        text = resp.choices[0].message.content.strip()
        match = _re.search(r'\{.*\}', text, _re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        logger.error(f"_generate_mejora_ai: {e}")
    return {}


def send_mejora_semanal():
    global _last_recommended_mejora
    send_message("🤖 Generando tu recomendación personalizada...")
    mejora = _generate_mejora_ai()
    if not mejora:
        send_message("❌ No pude generar la recomendación. Inténtalo de nuevo con /recomendar.")
        return

    # Guardar en Supabase para que aparezca en el dashboard
    if IS_CLOUD:
        try:
            from supabase_client import insert_mejora
            saved = insert_mejora(
                mejora["nombre"], mejora.get("categoria", "enfoque"),
                mejora.get("descripcion", ""), mejora.get("evidencia", ""),
            )
            mejora["id"] = saved.get("id")
        except Exception as e:
            logger.error(f"send_mejora_semanal insert: {e}")

    _last_recommended_mejora = mejora
    cat_emoji = {
        "testosterona": "⚡", "estres": "🧘", "recuperacion": "💪",
        "sueno": "😴", "enfoque": "🎯",
    }.get(mejora.get("categoria", ""), "💡")
    send_message(
        f"💡 <b>MEJORA DE LA SEMANA</b>\n"
        f"{'─' * 22}\n"
        f"{cat_emoji} <b>{mejora['nombre']}</b> "
        f"[{mejora.get('categoria','').upper()}]\n\n"
        f"{mejora.get('descripcion','')}\n\n"
        f"🔬 <b>¿Por qué funciona?</b>\n"
        f"<i>{mejora.get('evidencia','')}</i>\n\n"
        f"Si quieres intentarlo escribe: <code>quiero intentarlo</code>"
    )


def handle_intentar_mejora(text: str):
    global _last_recommended_mejora
    if not IS_CLOUD:
        send_message("⚠️ Esta función requiere modo cloud (Supabase).")
        return
    from supabase_client import update_mejora_estado
    mejora = _last_recommended_mejora
    if not mejora or not mejora.get("id"):
        send_message("Escribe /recomendar primero para recibir una mejora nueva.")
        return
    update_mejora_estado(mejora["id"], "en_proceso", datetime.now().date().isoformat())
    _last_recommended_mejora = {}
    send_message(
        f"✅ <b>¡Añadida a tus mejoras en proceso!</b>\n"
        f"💡 <b>{mejora['nombre']}</b>\n\n"
        f"{mejora.get('descripcion','')}\n\n"
        f"Te aparecerá en el dashboard y en el recordatorio diario. ¡Constancia!"
    )


# ── Ikigai interactivo ─────────────────────────────────────────
def _handle_ikigai_step(text: str) -> bool:
    """Maneja cada respuesta del flujo Ikigai. Devuelve True si estaba activo."""
    if not _ikigai_conv:
        return False

    step = _ikigai_conv.get("step", 0)
    _ikigai_conv["answers"][f"q{step + 1}"] = text.strip()
    next_step = step + 1
    _ikigai_conv["step"] = next_step

    if next_step < len(IKIGAI_STEPS):
        send_message(IKIGAI_STEPS[next_step]["pregunta"])
    else:
        send_message(
            "✨ <b>¡Perfecto! Has completado las 10 preguntas.</b>\n\n"
            "Ahora analizaré tus respuestas con detalle.\n"
            "Dame un momento... 🌸"
        )
        _generate_ikigai_analysis(_ikigai_conv["answers"].copy())
        _ikigai_conv.clear()

    return True


def _generate_ikigai_analysis(answers: dict):
    """Llama a Groq con las 10 respuestas y envía 3 mensajes de análisis. Guarda en Obsidian."""
    import re as _re

    respuestas_str = "\n".join(
        f"Pregunta {i} ({IKIGAI_STEPS[i-1]['circulo']}): {answers.get(f'q{i}', '')}"
        for i in range(1, 11)
    )

    prompt = (
        "Actúa como un coach de vida experto en el método Ikigai japonés.\n"
        "Basándote en estas respuestas REALES del usuario:\n\n"
        f"{respuestas_str}\n\n"
        "Genera un análisis ikigai profundo y personalizado. "
        "Devuelve SOLO un JSON válido con esta estructura exacta:\n"
        '{"lo_que_amas": ["3-4 elementos concretos identificados de sus respuestas"],'
        '"lo_que_se_te_da_bien": ["3-4 elementos concretos"],'
        '"lo_que_necesita_el_mundo": ["2-3 elementos concretos"],'
        '"por_lo_que_te_pueden_pagar": ["2-3 elementos concretos"],'
        '"ikigai_central": "1-2 frases que definen su propósito único y singular",'
        '"mision": "Intersección: lo que amas + lo que el mundo necesita (1-2 frases)",'
        '"vocacion": "Intersección: lo que amas + en lo que eres bueno (1-2 frases)",'
        '"profesion": "Intersección: en lo que eres bueno + por lo que te pagan (1-2 frases)",'
        '"pasion": "Intersección: lo que amas + por lo que te pagan (1-2 frases)",'
        '"pasos_accion": ["3 pasos concretos y específicos que puede hacer esta semana"],'
        '"reflexion_final": "Párrafo motivador y personalizado de 3-4 frases"}'
    )

    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1200,
            temperature=0.7,
        )
        raw = resp.choices[0].message.content.strip()
        match = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if not match:
            send_message("❌ No pude generar el análisis. Inténtalo de nuevo con /ikigai.")
            return
        data = json.loads(match.group())

        # Mensaje 1 — Los 4 círculos
        amas  = "\n".join(f"  • {x}" for x in data.get("lo_que_amas", []))
        bien  = "\n".join(f"  • {x}" for x in data.get("lo_que_se_te_da_bien", []))
        mundo = "\n".join(f"  • {x}" for x in data.get("lo_que_necesita_el_mundo", []))
        pagan = "\n".join(f"  • {x}" for x in data.get("por_lo_que_te_pueden_pagar", []))
        send_message(
            "🌸 <b>TU IKIGAI — LOS 4 CÍRCULOS</b>\n"
            f"{'─' * 24}\n\n"
            f"❤️ <b>Lo que AMAS:</b>\n{amas}\n\n"
            f"💪 <b>En lo que ERES BUENO:</b>\n{bien}\n\n"
            f"🌍 <b>Lo que el MUNDO NECESITA:</b>\n{mundo}\n\n"
            f"💰 <b>Por lo que te PUEDEN PAGAR:</b>\n{pagan}"
        )

        # Mensaje 2 — Intersecciones + centro
        send_message(
            "✨ <b>LAS INTERSECCIONES</b>\n"
            f"{'─' * 24}\n\n"
            f"🎯 <b>MISIÓN</b> (amor + mundo):\n{data.get('mision','')}\n\n"
            f"🎸 <b>VOCACIÓN</b> (amor + habilidad):\n{data.get('vocacion','')}\n\n"
            f"💼 <b>PROFESIÓN</b> (habilidad + dinero):\n{data.get('profesion','')}\n\n"
            f"🔥 <b>PASIÓN</b> (amor + dinero):\n{data.get('pasion','')}\n\n"
            f"{'─' * 24}\n"
            f"🌟 <b>TU IKIGAI CENTRAL:</b>\n<i>{data.get('ikigai_central','')}</i>"
        )

        # Mensaje 3 — Pasos + reflexión
        pasos = "\n".join(f"  {i+1}. {p}" for i, p in enumerate(data.get("pasos_accion", [])))
        send_message(
            f"🚀 <b>PASOS DE ACCIÓN PARA ESTA SEMANA</b>\n"
            f"{'─' * 24}\n\n"
            f"{pasos}\n\n"
            f"{'─' * 24}\n"
            f"💭 <b>Reflexión final:</b>\n<i>{data.get('reflexion_final','')}</i>"
        )

        # Guardar en Obsidian y Supabase
        _save_ikigai_to_obsidian(answers, data)
        if IS_CLOUD:
            try:
                from supabase_client import save_ikigai_resultado
                save_ikigai_resultado(data, answers)
            except Exception as _e:
                logger.error(f"save_ikigai_resultado: {_e}")

    except Exception as e:
        logger.error(f"_generate_ikigai_analysis: {e}")
        send_message("❌ Error generando el análisis Ikigai. Inténtalo más tarde.")


def _save_ikigai_to_obsidian(answers: dict, analysis: dict):
    """Guarda el resultado del Ikigai en libro-3-planificacion/ del vault."""
    try:
        from datetime import date
        fecha = date.today().strftime("%Y-%m-%d")
        vault = r"C:\Users\geost\Desktop\Obsidian SC Claude Code"
        ruta = os.path.join(vault, "libro-3-planificacion", f"ikigai-{fecha}.md")

        amas  = "\n".join(f"- {x}" for x in analysis.get("lo_que_amas", []))
        bien  = "\n".join(f"- {x}" for x in analysis.get("lo_que_se_te_da_bien", []))
        mundo = "\n".join(f"- {x}" for x in analysis.get("lo_que_necesita_el_mundo", []))
        pagan = "\n".join(f"- {x}" for x in analysis.get("por_lo_que_te_pueden_pagar", []))
        pasos = "\n".join(f"- {p}" for p in analysis.get("pasos_accion", []))
        preguntas = "\n".join(
            f"**P{i}** ({IKIGAI_STEPS[i-1]['circulo']}): {answers.get(f'q{i}','')}"
            for i in range(1, 11)
        )

        contenido = (
            f"---\n"
            f"title: Ikigai — {fecha}\n"
            f"tags: [\"#ikigai\", \"#proposito\", \"#libro-3\"]\n"
            f"---\n\n"
            f"# 🌸 Ikigai — {fecha}\n\n"
            f"## Los 4 Círculos\n\n"
            f"### ❤️ Lo que amo\n{amas}\n\n"
            f"### 💪 En lo que soy bueno\n{bien}\n\n"
            f"### 🌍 Lo que el mundo necesita\n{mundo}\n\n"
            f"### 💰 Por lo que me pueden pagar\n{pagan}\n\n"
            f"## ✨ Intersecciones\n\n"
            f"**🎯 Misión:** {analysis.get('mision','')}\n\n"
            f"**🎸 Vocación:** {analysis.get('vocacion','')}\n\n"
            f"**💼 Profesión:** {analysis.get('profesion','')}\n\n"
            f"**🔥 Pasión:** {analysis.get('pasion','')}\n\n"
            f"## 🌟 Ikigai Central\n\n"
            f"> {analysis.get('ikigai_central','')}\n\n"
            f"## 🚀 Pasos de Acción\n\n{pasos}\n\n"
            f"## 📝 Mis Respuestas\n\n{preguntas}\n\n"
            f"## 🔗 Relacionado\n\n"
            f"[[metas-{fecha[:4]}]] · [[planificacion-{fecha[:7]}]]\n"
        )

        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(contenido)
        logger.info(f"Ikigai guardado en: {ruta}")
    except Exception as e:
        logger.error(f"_save_ikigai_to_obsidian: {e}")


def handle_chat_question(text: str):
    """Modo conversacional libre — no extrae datos, solo responde la pregunta."""
    try:
        context = ""
        if IS_CLOUD:
            from supabase_client import get_vida_state_summary
            context = get_vida_state_summary()
        else:
            context = "Sistema en modo local."

        system_prompt = (
            f"Eres el asistente personal de {PLAYER_NAME}, experto en su Sistema de Vida.\n"
            f"Estado actual del sistema:\n{context}\n\n"
            "Responde en español, de forma directa y útil. "
            "Si pregunta sobre sus datos, úsalos. "
            "Si da consejos de salud o fitness, fundamenta con evidencia científica. "
            "Sé amigable y motivador. Usa HTML Telegram (<b>, <i>) para formatear."
        )
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": text},
            ],
            max_tokens=600,
            temperature=0.7,
        )
        answer = resp.choices[0].message.content.strip()
        send_message(f"🤖 <b>Asistente IA</b>\n{'─' * 22}\n{answer}")
    except Exception as e:
        logger.error(f"handle_chat_question: {e}")
        send_message("❌ Error al procesar la pregunta. Inténtalo de nuevo.")


# ── Recordatorio diario ────────────────────────────────────────
def send_daily_reminder():
    global last_entry_date
    today = datetime.now().date()
    if last_entry_date != today:
        xp_state  = load_state()
        ov_level, ov_name = overall_level(xp_state)
        streaks = xp_state.get("streaks", {})

        # Construir línea de mejoras activas
        mejoras_linea = ""
        if IS_CLOUD:
            try:
                from supabase_client import get_mejoras
                activas = get_mejoras("activa") + get_mejoras("en_proceso")
                if activas:
                    nombres = " · ".join(m["nombre"] for m in activas[:3])
                    mejoras_linea = f"• Mejoras en proceso: {nombres}\n"
            except Exception:
                pass

        racha_creatina = streaks.get("creatina", 0)
        creatina_linea = f"• Creatina 💊 (racha: {racha_creatina}d)\n" if racha_creatina >= 0 else ""

        send_message(
            f"🔔 <b>Recordatorio del sistema de vida</b>\n\n"
            f"⚔️ Nivel {ov_level} — {ov_name} | {xp_state['total_xp']:,} XP\n\n"
            "¿Ya registraste tu día? Cuéntame:\n"
            "• Qué comiste 🍽️ (macros si los tienes)\n"
            "• Si entrenaste 💪\n"
            "• Gastos del día 💶\n"
            "• Hábitos: ducha fría 🚿, té clavo 🌿, oración 🙏\n"
            f"{creatina_linea}"
            f"{mejoras_linea}"
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
    schedule.every().day.at(CREATINE_TIME).do(send_creatine_reminder)
    schedule.every().sunday.at("10:00").do(send_mejora_semanal)
    schedule.every().day.at(DATO_MAÑANA).do(send_dato_curioso, slot="mañana")
    schedule.every().day.at(DATO_TARDE).do(send_dato_curioso, slot="tarde")
    schedule.every().day.at(DATO_NOCHE).do(send_dato_curioso, slot="noche")

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

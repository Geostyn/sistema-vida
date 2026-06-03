"""
vida_xp.py — Motor de gamificación del Sistema de Vida.

Gestiona XP, niveles, habilidades y logros como un RPG real.
Los datos persisten en vida_xp.json (auto-generado en la misma carpeta).
"""
import os
import json
from datetime import date, datetime
from typing import Optional

XP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vida_xp.json")
VAULT   = r"C:\Users\geost\Desktop\Obsidian SC Claude Code"

# ── Definición de habilidades ───────────────────────────────
SKILLS = {
    "fitness":    {"emoji": "💪", "nombre": "Fitness",    "desc": "Deporte y condición física"},
    "nutricion":  {"emoji": "🥗", "nombre": "Nutrición",  "desc": "Alimentación y macros"},
    "sabiduria":  {"emoji": "🧠", "nombre": "Sabiduría",  "desc": "Léxico, refranes, conocimiento"},
    "riqueza":    {"emoji": "💰", "nombre": "Riqueza",    "desc": "Finanzas, ahorro, inversión"},
    "empresario": {"emoji": "🚀", "nombre": "Empresario", "desc": "Ideas y proyectos de negocio"},
    "trader":     {"emoji": "📈", "nombre": "Trader",     "desc": "Trading y análisis de mercados"},
    "alma":       {"emoji": "🙏", "nombre": "Alma",       "desc": "Espiritualidad, fe y crecimiento interior"},
    "disciplina": {"emoji": "⚡", "nombre": "Disciplina", "desc": "Constancia, hábitos y rachas"},
}

# ── Tabla de XP por acción ──────────────────────────────────
XP_ACTIONS = {
    # FITNESS
    "deporte_registrado":      {"xp": 15, "skill": "fitness",    "msg": "+15 XP 💪 Fitness"},
    "deporte_excelente":       {"xp": 25, "skill": "fitness",    "msg": "+25 XP 💪 ¡Sesión épica!"},
    # NUTRICIÓN
    "alimentacion_registrada": {"xp": 10, "skill": "nutricion",  "msg": "+10 XP 🥗 Nutrición"},
    "proteina_objetivo":       {"xp": 20, "skill": "nutricion",  "msg": "+20 XP 🥗 ¡Target de proteína alcanzado!"},
    "agua_objetivo":           {"xp": 10, "skill": "nutricion",  "msg": "+10 XP 💧 Hidratado"},
    # SABIDURÍA
    "lexico_palabra":          {"xp": 10, "skill": "sabiduria",  "msg": "+10 XP 🧠 Sabiduría"},
    "refran_aprendido":        {"xp": 10, "skill": "sabiduria",  "msg": "+10 XP 🧠 Nuevo refrán"},
    # RIQUEZA
    "gasto_registrado":        {"xp": 8,  "skill": "riqueza",    "msg": "+8 XP 💰 Riqueza"},
    "en_presupuesto":          {"xp": 25, "skill": "riqueza",    "msg": "+25 XP 💰 ¡Dentro del presupuesto!"},
    # EMPRESARIO
    "idea_nueva":              {"xp": 15, "skill": "empresario", "msg": "+15 XP 🚀 Empresario"},
    # TRADER
    "reflexion_trading":       {"xp": 10, "skill": "trader",     "msg": "+10 XP 📈 Trader"},
    # ALMA
    "oracion_cumplida":        {"xp": 15, "skill": "alma",       "msg": "+15 XP 🙏 Alma"},
    "silencio_cumplido":       {"xp": 10, "skill": "alma",       "msg": "+10 XP 🙏 Reflexión"},
    # DISCIPLINA
    "ducha_fria":              {"xp": 15, "skill": "disciplina", "msg": "+15 XP ⚡ Disciplina"},
    "te_clavo":                {"xp": 8,  "skill": "disciplina", "msg": "+8 XP ⚡ Hábito cumplido"},
    "diario_escrito":          {"xp": 10, "skill": "disciplina", "msg": "+10 XP ⚡ Día registrado"},
}

# ── Tabla de niveles ────────────────────────────────────────
LEVELS = [
    (0,      1,  "Principiante"),
    (100,    2,  "Aprendiz"),
    (250,    3,  "Iniciado"),
    (500,    4,  "Practicante"),
    (1000,   5,  "Competente"),
    (2000,   6,  "Experto"),
    (4000,   7,  "Maestro"),
    (8000,   8,  "Gran Maestro"),
    (15000,  9,  "Leyenda"),
    (30000,  10, "Inmortal"),
]

# ── Logros ──────────────────────────────────────────────────
ACHIEVEMENTS = {
    "first_workout": {
        "emoji": "🏃", "nombre": "Primer Paso",
        "desc": "Registra tu primer entrenamiento",
        "xp_bonus": 25, "skill_bonus": "fitness"
    },
    "cold_warrior_3": {
        "emoji": "🧊", "nombre": "Valiente",
        "desc": "3 días seguidos con ducha fría",
        "xp_bonus": 30, "skill_bonus": "disciplina"
    },
    "cold_warrior_7": {
        "emoji": "❄️", "nombre": "Guerrero del Frío",
        "desc": "7 días seguidos con ducha fría",
        "xp_bonus": 75, "skill_bonus": "disciplina"
    },
    "cold_warrior_30": {
        "emoji": "🏔️", "nombre": "Bañista Ártico",
        "desc": "30 días seguidos con ducha fría",
        "xp_bonus": 300, "skill_bonus": "disciplina"
    },
    "protein_goal_3": {
        "emoji": "🍗", "nombre": "Máquina de Músculo",
        "desc": "3 días seguidos cumpliendo el target de proteína",
        "xp_bonus": 50, "skill_bonus": "nutricion"
    },
    "protein_goal_7": {
        "emoji": "💪", "nombre": "Semana de Hierro",
        "desc": "7 días seguidos con proteína al objetivo",
        "xp_bonus": 100, "skill_bonus": "nutricion"
    },
    "lexico_10": {
        "emoji": "📚", "nombre": "Lector Curioso",
        "desc": "10 palabras aprendidas en el léxico",
        "xp_bonus": 40, "skill_bonus": "sabiduria"
    },
    "lexico_50": {
        "emoji": "🎓", "nombre": "Erudito",
        "desc": "50 palabras en el léxico",
        "xp_bonus": 150, "skill_bonus": "sabiduria"
    },
    "refran_5": {
        "emoji": "💬", "nombre": "Sabio Popular",
        "desc": "5 refranes aprendidos",
        "xp_bonus": 40, "skill_bonus": "sabiduria"
    },
    "refran_20": {
        "emoji": "🏛️", "nombre": "Filósofo",
        "desc": "20 refranes en tu colección",
        "xp_bonus": 100, "skill_bonus": "sabiduria"
    },
    "first_idea": {
        "emoji": "💡", "nombre": "El Emprendedor",
        "desc": "Primera idea de negocio registrada",
        "xp_bonus": 30, "skill_bonus": "empresario"
    },
    "ideas_5": {
        "emoji": "🔥", "nombre": "Visionario",
        "desc": "5 ideas de negocio en el backlog",
        "xp_bonus": 100, "skill_bonus": "empresario"
    },
    "prayer_7": {
        "emoji": "🕊️", "nombre": "Fiel",
        "desc": "7 días seguidos de oración",
        "xp_bonus": 75, "skill_bonus": "alma"
    },
    "prayer_30": {
        "emoji": "✝️", "nombre": "Fe Inquebrantable",
        "desc": "30 días seguidos de oración",
        "xp_bonus": 250, "skill_bonus": "alma"
    },
    "habit_all_7": {
        "emoji": "🌟", "nombre": "Semana Perfecta",
        "desc": "7 días cumpliendo TODOS los hábitos",
        "xp_bonus": 150, "skill_bonus": "disciplina"
    },
    "habit_all_30": {
        "emoji": "👑", "nombre": "Hábito Forjado",
        "desc": "30 días cumpliendo TODOS los hábitos",
        "xp_bonus": 600, "skill_bonus": "disciplina"
    },
    "savings_month": {
        "emoji": "💎", "nombre": "Primer Ahorro",
        "desc": "Primer mes dentro del presupuesto con ahorro",
        "xp_bonus": 100, "skill_bonus": "riqueza"
    },
    "level5_all": {
        "emoji": "⚔️", "nombre": "Guerrero Completo",
        "desc": "Nivel 5 o más en todas las habilidades",
        "xp_bonus": 500, "skill_bonus": "disciplina"
    },
    "level10_any": {
        "emoji": "🏆", "nombre": "Maestría",
        "desc": "Nivel 10 (Inmortal) en cualquier habilidad",
        "xp_bonus": 1000, "skill_bonus": "disciplina"
    },
}


# ── Estado inicial ──────────────────────────────────────────
def _default_state() -> dict:
    return {
        "version": 1,
        "total_xp": 0,
        "overall_level": 1,
        "skills": {sk: {"xp": 0, "level": 1} for sk in SKILLS},
        "achievements_unlocked": [],
        "streaks": {
            "ducha_fria": 0, "te_clavo": 0, "oracion": 0,
            "silencio": 0, "habitos_todos": 0, "proteina": 0
        },
        "counters": {
            "lexico": 0, "refranes": 0, "ideas": 0,
            "entrenamientos": 0, "dias_diario": 0
        },
        "last_updated": str(date.today()),
    }


def load_state() -> dict:
    if os.path.exists(XP_FILE):
        try:
            with open(XP_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            # Migrate: ensure ALL keys from default state exist
            defaults = _default_state()
            for k, v in defaults.items():
                state.setdefault(k, v)
            for sk in SKILLS:
                state["skills"].setdefault(sk, {"xp": 0, "level": 1})
            for k, v in defaults["streaks"].items():
                state["streaks"].setdefault(k, v)
            for k, v in defaults["counters"].items():
                state["counters"].setdefault(k, v)
            return state
        except Exception:
            pass
    state = _default_state()
    save_state(state)
    return state


def save_state(state: dict):
    state["last_updated"] = str(date.today())
    with open(XP_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── Cálculo de nivel ────────────────────────────────────────
def xp_to_level(xp: int) -> tuple[int, str, int, int]:
    """Devuelve (nivel, nombre, xp_actual_en_nivel, xp_para_siguiente)."""
    level_num, level_name = 1, "Principiante"
    for threshold, num, name in LEVELS:
        if xp >= threshold:
            level_num, level_name = num, name

    # XP para siguiente nivel
    current_threshold = next((t for t, n, _ in LEVELS if n == level_num), 0)
    next_threshold = next((t for t, n, _ in LEVELS if n == level_num + 1), None)

    xp_in_level = xp - current_threshold
    xp_to_next  = (next_threshold - current_threshold) if next_threshold else 0
    return level_num, level_name, xp_in_level, xp_to_next


def overall_level(state: dict) -> tuple[int, str]:
    """Nivel global basado en XP total."""
    return xp_to_level(state["total_xp"])[:2]


# ── Dar XP ──────────────────────────────────────────────────
def award_xp(action_key: str, state: Optional[dict] = None) -> dict:
    """
    Premia XP por una acción. Devuelve dict con:
    {xp_gained, skill, leveled_up, new_level, achievements_unlocked, messages}
    """
    if state is None:
        state = load_state()

    if action_key not in XP_ACTIONS:
        return {"xp_gained": 0, "messages": [], "achievements_unlocked": []}

    action     = XP_ACTIONS[action_key]
    xp_gained  = action["xp"]
    skill      = action["skill"]
    messages   = [action["msg"]]
    new_achievs = []

    # Sumar XP
    state["total_xp"] += xp_gained
    state["skills"][skill]["xp"] += xp_gained

    # Verificar level up en la habilidad
    old_level = state["skills"][skill]["level"]
    new_level_num, new_level_name, _, _ = xp_to_level(state["skills"][skill]["xp"])
    leveled_up = new_level_num > old_level

    if leveled_up:
        state["skills"][skill]["level"] = new_level_num
        messages.append(
            f"🎉 ¡LEVEL UP! {SKILLS[skill]['emoji']} {SKILLS[skill]['nombre']} "
            f"→ Nivel {new_level_num} ({new_level_name})"
        )

    # Verificar overall level
    ov_old = state["overall_level"]
    ov_new, ov_name = overall_level(state)
    if ov_new > ov_old:
        state["overall_level"] = ov_new
        messages.append(f"⭐ ¡NIVEL GLOBAL {ov_new} — {ov_name}!")

    save_state(state)
    return {
        "xp_gained": xp_gained, "skill": skill,
        "leveled_up": leveled_up, "new_level": new_level_num,
        "achievements_unlocked": new_achievs, "messages": messages,
        "state": state
    }


# ── Actualizar contadores y rachas ──────────────────────────
def update_streak(streak_key: str, achieved: bool, state: Optional[dict] = None) -> dict:
    """Actualiza una racha y comprueba logros."""
    if state is None:
        state = load_state()

    if achieved:
        state["streaks"][streak_key] = state["streaks"].get(streak_key, 0) + 1
    else:
        state["streaks"][streak_key] = 0

    messages = check_achievements(state)
    save_state(state)
    return {"streaks": state["streaks"], "messages": messages}


def increment_counter(counter_key: str, state: Optional[dict] = None) -> dict:
    """Incrementa un contador y comprueba logros."""
    if state is None:
        state = load_state()

    state["counters"][counter_key] = state["counters"].get(counter_key, 0) + 1
    messages = check_achievements(state)
    save_state(state)
    return {"counter": state["counters"][counter_key], "messages": messages}


# ── Comprobar y desbloquear logros ──────────────────────────
def check_achievements(state: dict) -> list[str]:
    """Comprueba si se han desbloqueado nuevos logros. Devuelve mensajes."""
    messages = []
    unlocked = set(state.get("achievements_unlocked", []))

    def try_unlock(achievement_id: str):
        if achievement_id not in unlocked:
            ach = ACHIEVEMENTS[achievement_id]
            unlocked.add(achievement_id)
            state["achievements_unlocked"] = list(unlocked)
            # Dar XP bonus
            state["total_xp"] += ach["xp_bonus"]
            skill = ach.get("skill_bonus", "disciplina")
            if skill in state["skills"]:
                state["skills"][skill]["xp"] += ach["xp_bonus"]
            messages.append(
                f"🏆 ¡LOGRO DESBLOQUEADO! {ach['emoji']} <b>{ach['nombre']}</b>\n"
                f"   {ach['desc']}\n"
                f"   +{ach['xp_bonus']} XP Bonus"
            )

    # Comprobar logros basados en contadores y rachas
    streaks  = state.get("streaks", {})
    counters = state.get("counters", {})
    skills   = state.get("skills", {})

    if counters.get("entrenamientos", 0) >= 1:
        try_unlock("first_workout")
    if streaks.get("ducha_fria", 0) >= 3:
        try_unlock("cold_warrior_3")
    if streaks.get("ducha_fria", 0) >= 7:
        try_unlock("cold_warrior_7")
    if streaks.get("ducha_fria", 0) >= 30:
        try_unlock("cold_warrior_30")
    if streaks.get("proteina", 0) >= 3:
        try_unlock("protein_goal_3")
    if streaks.get("proteina", 0) >= 7:
        try_unlock("protein_goal_7")
    if counters.get("lexico", 0) >= 10:
        try_unlock("lexico_10")
    if counters.get("lexico", 0) >= 50:
        try_unlock("lexico_50")
    if counters.get("refranes", 0) >= 5:
        try_unlock("refran_5")
    if counters.get("refranes", 0) >= 20:
        try_unlock("refran_20")
    if counters.get("ideas", 0) >= 1:
        try_unlock("first_idea")
    if counters.get("ideas", 0) >= 5:
        try_unlock("ideas_5")
    if streaks.get("oracion", 0) >= 7:
        try_unlock("prayer_7")
    if streaks.get("oracion", 0) >= 30:
        try_unlock("prayer_30")
    if streaks.get("habitos_todos", 0) >= 7:
        try_unlock("habit_all_7")
    if streaks.get("habitos_todos", 0) >= 30:
        try_unlock("habit_all_30")
    if all(skills.get(sk, {}).get("level", 1) >= 5 for sk in SKILLS):
        try_unlock("level5_all")
    if any(skills.get(sk, {}).get("level", 1) >= 10 for sk in SKILLS):
        try_unlock("level10_any")

    return messages


# ── Construir el perfil completo ────────────────────────────
def build_profile_text(state: dict) -> str:
    """Genera el texto del perfil del aventurero para Obsidian."""
    ov_level, ov_name = overall_level(state)
    total_xp = state["total_xp"]
    _, _, xp_in, xp_next = xp_to_level(total_xp)
    bar_filled = int((xp_in / xp_next * 20)) if xp_next else 20
    bar = "█" * bar_filled + "░" * (20 - bar_filled)

    skills_rows = ""
    for sk_id, sk_info in SKILLS.items():
        sk_data   = state["skills"].get(sk_id, {"xp": 0, "level": 1})
        sk_xp     = sk_data["xp"]
        sk_lvl, sk_name, sk_in, sk_next = xp_to_level(sk_xp)
        bar_f     = int((sk_in / sk_next * 10)) if sk_next else 10
        sk_bar    = "█" * bar_f + "░" * (10 - bar_f)
        skills_rows += f"| {sk_info['emoji']} {sk_info['nombre']} | {sk_lvl} — {sk_name} | {sk_xp} XP | {sk_bar} |\n"

    unlocked = state.get("achievements_unlocked", [])
    ach_unlocked = ""
    ach_locked   = ""
    for ach_id, ach in ACHIEVEMENTS.items():
        if ach_id in unlocked:
            ach_unlocked += f"- {ach['emoji']} **{ach['nombre']}** — {ach['desc']}\n"
        else:
            ach_locked += f"- [ ] {ach['emoji']} **{ach['nombre']}** — {ach['desc']} *(+{ach['xp_bonus']} XP)*\n"

    if not ach_unlocked:
        ach_unlocked = "*(Ningún logro aún — ¡empieza hoy!)*\n"

    streaks = state.get("streaks", {})
    counters = state.get("counters", {})

    return f"""---
title: ⚔️ Perfil del Aventurero
tags:
  - sistema-de-vida
  - xp
  - gamificacion
  - estado/activo
fecha-inicio: 2026-06-01
---

# ⚔️ Perfil del Aventurero

> [!quote] "El personaje que juegas en la vida real es el que más importa."

---

## 🌟 Nivel Global

| Campo | Valor |
|-------|-------|
| **Nivel** | **{ov_level} — {ov_name}** |
| **XP Total** | {total_xp:,} XP |
| **Progreso al siguiente** | {xp_in}/{xp_next} XP |

**`[{bar}]`** {xp_in}/{xp_next if xp_next else "MAX"} XP

---

## 📊 Estadísticas de Habilidades

| Habilidad | Nivel | XP | Progreso (10 bloques) |
|-----------|-------|----|-----------------------|
{skills_rows}
---

## 📈 Rachas Actuales

| Hábito | Racha |
|--------|-------|
| 🚿 Ducha fría | {streaks.get('ducha_fria', 0)} días |
| 🌿 Té de clavo | {streaks.get('te_clavo', 0)} días |
| 🙏 Oración | {streaks.get('oracion', 0)} días |
| 💪 Proteína objetivo | {streaks.get('proteina', 0)} días |
| 🌟 Todos los hábitos | {streaks.get('habitos_todos', 0)} días |

---

## 📦 Contadores

| Ítem | Total |
|------|-------|
| 🏋️ Entrenamientos registrados | {counters.get('entrenamientos', 0)} |
| 📖 Palabras en el léxico | {counters.get('lexico', 0)} |
| 💬 Refranes aprendidos | {counters.get('refranes', 0)} |
| 💼 Ideas de negocio | {counters.get('ideas', 0)} |
| 📋 Días del diario escritos | {counters.get('dias_diario', 0)} |

---

## 🏆 Logros Desbloqueados

{ach_unlocked}
---

## 🔒 Logros Por Desbloquear

{ach_locked}
---

## 🔗 Relacionado

- [[sistema-de-vida]]
- [[libro-1-cuerpo-mente-alma/00-indice|📗 Libro 1]]
- [[libro-2-finanzas-negocios/00-indice|📒 Libro 2]]
- [[libro-3-planificacion/00-indice|📘 Libro 3]]

*Auto-actualizado cada vez que se registra una acción en el sistema.*
"""


def update_profile_file(state: dict):
    """Escribe el perfil del aventurero en Obsidian."""
    path    = os.path.join(VAULT, "perfil-aventurero.md")
    content = build_profile_text(state)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def get_xp_summary(state: dict) -> str:
    """Resumen corto del estado XP para incluir en mensajes del bot."""
    ov_level, ov_name = overall_level(state)
    top_skill = max(state["skills"].items(), key=lambda x: x[1]["xp"])
    top_name  = SKILLS[top_skill[0]]["emoji"] + " " + SKILLS[top_skill[0]]["nombre"]
    return (
        f"⚔️ Nivel {ov_level} ({ov_name}) · {state['total_xp']:,} XP total · "
        f"Mejor habilidad: {top_name} Nv.{top_skill[1]['level']}"
    )

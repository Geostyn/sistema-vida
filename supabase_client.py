"""
supabase_client.py — Operaciones cloud del Sistema de Vida.
Usado por vida_bot.py cuando corre en Fly.io (IS_CLOUD=True).
"""
import os
import json
import logging
from datetime import date

logger = logging.getLogger("vida_bot")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

_client = None


def get_client():
    global _client
    if _client is None:
        from supabase import create_client
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def _today() -> str:
    return date.today().isoformat()


def _num(v):
    try:
        return float(str(v).replace("g", "").replace("kcal", "").replace("L", "").strip()) if v else None
    except (ValueError, TypeError):
        return None


# ── XP State ─────────────────────────────────────────────────

def get_xp_state() -> dict:
    try:
        r = get_client().table("xp_state").select("state_json").eq("id", 1).execute()
        return r.data[0]["state_json"] if r.data else {}
    except Exception as e:
        logger.error(f"Supabase get_xp_state: {e}")
        return {}


def save_xp_state(state: dict):
    try:
        get_client().table("xp_state").upsert({"id": 1, "state_json": state}).execute()
    except Exception as e:
        logger.error(f"Supabase save_xp_state: {e}")


def sync_xp_from_supabase(xp_json_path: str):
    """Pull de Supabase → escribe vida_xp.json local (para que vida_xp.py lo lea)."""
    state = get_xp_state()
    if state:
        with open(xp_json_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)


def sync_xp_to_supabase(xp_json_path: str):
    """Lee vida_xp.json local → push a Supabase."""
    try:
        with open(xp_json_path, "r", encoding="utf-8") as f:
            save_xp_state(json.load(f))
    except FileNotFoundError:
        pass


# ── Insertores ───────────────────────────────────────────────

def insert_deporte(actividad, duracion, distancia, sensacion, notas) -> bool:
    try:
        get_client().table("deporte").insert({
            "fecha": _today(), "actividad": actividad, "duracion": duracion,
            "distancia": distancia, "sensacion": sensacion, "notas": notas,
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase insert_deporte: {e}")
        return False


def insert_alimentacion(desayuno, comida, cena, snacks, kcal, prot, carbs, grasas, agua, energia) -> bool:
    try:
        get_client().table("alimentacion").insert({
            "fecha": _today(), "desayuno": desayuno, "comida": comida,
            "cena": cena, "snacks": snacks, "kcal": _num(kcal),
            "prot_g": _num(prot), "carbs_g": _num(carbs),
            "grasas_g": _num(grasas), "agua_l": _num(agua), "energia": energia,
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase insert_alimentacion: {e}")
        return False


def insert_gasto(categoria, concepto, importe) -> bool:
    try:
        get_client().table("gastos").insert({
            "fecha": _today(), "categoria": categoria,
            "concepto": concepto, "importe": float(importe),
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase insert_gasto: {e}")
        return False


def insert_lexico(palabra, definicion, ejemplo) -> bool:
    try:
        get_client().table("lexico").insert({
            "fecha": _today(), "palabra": palabra,
            "definicion": definicion, "ejemplo": ejemplo,
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase insert_lexico: {e}")
        return False


def insert_refran(refran, significado, contexto) -> bool:
    try:
        get_client().table("refranes").insert({
            "fecha": _today(), "refran": refran,
            "significado": significado, "contexto": contexto,
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase insert_refran: {e}")
        return False


def insert_idea(idea, inversion, tiempo, potencial) -> bool:
    try:
        get_client().table("ideas_negocio").insert({
            "fecha": _today(), "idea": idea, "inversion": inversion,
            "tiempo_monetizacion": tiempo, "potencial": potencial,
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase insert_idea: {e}")
        return False


def insert_diario(lo_importante, gratitud, mejora, habitos_ok) -> bool:
    try:
        get_client().table("diario").insert({
            "fecha": _today(), "lo_importante": lo_importante,
            "gratitud": gratitud, "mejora": mejora, "habitos_ok": habitos_ok,
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase insert_diario: {e}")
        return False


def insert_habitos(ducha_fria, te_clavo, oracion, silencio) -> bool:
    try:
        get_client().table("habitos").insert({
            "fecha": _today(), "ducha_fria": ducha_fria, "te_clavo": te_clavo,
            "oracion": oracion, "silencio": silencio,
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase insert_habitos: {e}")
        return False


# ── Resumen de estado para el bot ────────────────────────────

def get_vida_state_summary() -> str:
    """Genera texto de contexto para el system prompt de Groq."""
    try:
        sb = get_client()
        month_start = _today()[:8] + "01"

        xp = get_xp_state()
        streaks = xp.get("streaks", {})

        gastos_r = sb.table("gastos").select("importe").gte("fecha", month_start).execute()
        total_gastos = sum(r["importe"] for r in gastos_r.data) if gastos_r.data else 0.0

        dep_r = sb.table("deporte").select("id", count="exact").gte("fecha", month_start).execute()
        alim_r = sb.table("alimentacion").select("id", count="exact").gte("fecha", month_start).execute()

        def streak(key):
            v = streaks.get(key, 0)
            return v if isinstance(v, (int, float)) else v.get("current", 0)

        return (
            f"MES: {_today()[:7]}\n"
            f"STREAKS: ducha_fria={streak('ducha_fria')}d | te_clavo={streak('te_clavo')}d | oracion={streak('oracion')}d\n"
            f"ENTRENOS_MES: {dep_r.count or 0}\n"
            f"DIETA_DIAS_MES: {alim_r.count or 0}\n"
            f"GASTOS_MES: €{total_gastos:.2f}\n"
            f"TARGET_NUTRI: 2800kcal|150gProt|350gCarbs|78gGrasas|3L agua\n"
            f"OBJETIVO: ganar masa muscular\n"
            f"XP_TOTAL: {xp.get('total_xp', 0)}"
        )
    except Exception as e:
        logger.error(f"Supabase get_vida_state_summary: {e}")
        return "Estado no disponible temporalmente."

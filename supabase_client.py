"""
supabase_client.py — Operaciones cloud del Sistema de Vida.
Usado por vida_bot.py cuando corre en Fly.io (IS_CLOUD=True).
"""
import os
import re
import json
import time
import uuid
import logging
import unicodedata
from datetime import date, datetime, timedelta, timezone

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


def _peso_num(v):
    try:
        s = str(v).lower().replace("kg", "").replace(",", ".").strip()
        return float(s) if s else None
    except (ValueError, TypeError):
        return None


# Palabras vacías que no aportan a la identidad del ejercicio.
_FILLERS_EJ = {"de", "del", "la", "el", "los", "las", "con", "en", "a", "al",
               "y", "para", "por", "un", "una", "unos", "unas"}


def _norm_ejercicio(s: str) -> str:
    """Normaliza el nombre de un ejercicio para que las variantes se unifiquen.
    Quita acentos, baja a minúsculas y elimina relleno: 'Curl de Bíceps',
    'curl biceps' y 'Curl Bíceps' → 'curl biceps'. (Espejo de normKey() en la web.)"""
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    limpio = "".join(ch if ch.isalnum() else " " for ch in s)
    tokens = [t for t in limpio.split() if t and t not in _FILLERS_EJ]
    return " ".join(tokens).strip()


# ── Clasificación por grupo muscular ─────────────────────────
# Palabra clave (en el nombre del ejercicio) → grupo muscular.
# El orden importa: se evalúa por coincidencia de subcadena.
_MUSCULO_KEYWORDS = [
    ("hombro",  ["press militar", "press hombro", "elevacion lateral", "elevación lateral",
                 "elevacion frontal", "elevación frontal", "pajaro", "pájaro", "face pull",
                 "press arnold", "hombro", "deltoid"]),
    ("espalda", ["dominada", "dominadas", "jalon", "jalón", "remo", "peso muerto",
                 "pull over", "pullover", "espalda", "dorsal", "trapecio", "encogimiento"]),
    ("pecho",   ["press banca", "press de banca", "press inclinado", "press plano",
                 "aperturas", "aperture", "pec deck", "peck deck", "pec fly", "peck fly",
                 "pecfly", "peckfly", "butterfly", "contractor", "contractora",
                 "cruce de poleas", "cruces de polea", "fondos pecho",
                 "press pecho", "pecho", "pectoral"]),
    ("biceps",  ["curl de biceps", "curl de bíceps", "curl biceps", "curl bíceps",
                 "curl martillo", "curl predicador", "curl concentrado", "curl", "biceps", "bíceps"]),
    ("triceps", ["fondos", "extension triceps", "extensión tríceps", "press frances",
                 "press francés", "patada triceps", "jalon triceps", "triceps", "tríceps"]),
    ("pierna",  ["sentadilla", "squat", "prensa", "zancada", "zancadas", "lunge",
                 "extension cuadriceps", "extensión cuádriceps", "curl femoral",
                 "gemelo", "gemelos", "peso muerto rumano", "hip thrust", "pierna",
                 "cuadriceps", "cuádriceps", "femoral", "gluteo", "glúteo"]),
    ("abdomen", ["abdominal", "abdominales", "plancha", "crunch", "elevacion piernas",
                 "elevación piernas", "rueda abdominal", "abdomen", "core"]),
]


_GRUPOS_VALIDOS = {"hombro", "pecho", "espalda", "biceps", "triceps", "pierna", "abdomen", "otros"}


def normalizar_grupo(valor: str) -> str:
    """Normaliza un grupo muscular (p.ej. el que devuelve la IA) al conjunto canónico.
    Devuelve '' si no se reconoce."""
    g = str(valor or "").strip().lower()
    g = (g.replace("bíceps", "biceps").replace("tríceps", "triceps")
           .replace("piernas", "pierna").replace("abdominales", "abdomen")
           .replace("abdominal", "abdomen").replace("hombros", "hombro")
           .replace("pectoral", "pecho").replace("dorsal", "espalda")
           .replace("gluteo", "pierna").replace("glúteo", "pierna")
           .replace("cuadriceps", "pierna").replace("femoral", "pierna").strip())
    return g if g in _GRUPOS_VALIDOS else ""


def clasificar_grupo_muscular(ejercicio: str) -> str:
    """Devuelve el grupo muscular de un ejercicio según palabras clave. 'otros' si no encaja."""
    ej = str(ejercicio).strip().lower()
    if not ej:
        return "otros"
    for grupo, claves in _MUSCULO_KEYWORDS:
        if any(k in ej for k in claves):
            return grupo
    return "otros"


def insert_gym_set(ejercicio, reps, peso, notas="", grupo_muscular="") -> int:
    """Inserta UNA serie de un ejercicio de gym. Si el mismo ejercicio ya tiene
    series registradas hoy, la añade como la siguiente serie.
    El grupo muscular se decide así: primero el diccionario de palabras clave
    (determinista); si da 'otros', se usa el grupo que aporta la IA.
    Devuelve el número de serie asignado (0 si falló)."""
    try:
        ej = _norm_ejercicio(ejercicio)
        grupo = clasificar_grupo_muscular(ej)
        if grupo == "otros":
            grupo = normalizar_grupo(grupo_muscular) or "otros"
        r = get_client().table("gym_ejercicios").select("serie") \
            .eq("fecha", _today()).eq("ejercicio", ej).execute()
        serie = (max((row.get("serie") or 0) for row in r.data) + 1) if r.data else 1
        try:
            reps_n = int(float(str(reps).replace("reps", "").strip())) if reps else None
        except (ValueError, TypeError):
            reps_n = None
        get_client().table("gym_ejercicios").insert({
            "fecha": _today(), "ejercicio": ej, "serie": serie,
            "reps": reps_n, "peso": _peso_num(peso), "notas": notas or "",
            "grupo_muscular": grupo,
        }).execute()
        return serie
    except Exception as e:
        logger.error(f"Supabase insert_gym_set: {e}")
        return 0


def get_historial_ejercicio(ejercicio: str, limite_dias: int = 5) -> list:
    """Últimas sesiones (por fecha) de un ejercicio: para cada día, peso máx,
    reps máx y nº de series. Más reciente primero. Sirve para sobrecarga progresiva."""
    try:
        ej = _norm_ejercicio(ejercicio)
        r = get_client().table("gym_ejercicios").select("fecha,reps,peso,serie") \
            .eq("ejercicio", ej).order("fecha", desc=True).limit(300).execute()
        por_dia = {}
        for row in (r.data or []):
            f = row.get("fecha")
            if not f:
                continue
            d = por_dia.setdefault(f, {"fecha": f, "peso_max": 0.0, "reps_max": 0, "series": 0})
            d["series"] += 1
            d["peso_max"] = max(d["peso_max"], float(row.get("peso") or 0))
            d["reps_max"] = max(d["reps_max"], int(row.get("reps") or 0))
        dias = sorted(por_dia.values(), key=lambda x: x["fecha"], reverse=True)
        return dias[:limite_dias]
    except Exception as e:
        logger.error(f"Supabase get_historial_ejercicio: {e}")
        return []


def get_ultima_sesion_resumen() -> list:
    """Resumen de la ÚLTIMA fecha con gym (distinta de hoy): lista de
    {ejercicio, peso_max, reps_max, series} para arrancar la sesión con objetivos."""
    try:
        r = get_client().table("gym_ejercicios").select("fecha,ejercicio,reps,peso") \
            .neq("fecha", _today()).order("fecha", desc=True).limit(200).execute()
        data = r.data or []
        if not data:
            return []
        ultima_fecha = data[0]["fecha"]
        por_ej = {}
        for row in data:
            if row.get("fecha") != ultima_fecha:
                continue
            ej = row.get("ejercicio", "")
            d = por_ej.setdefault(ej, {"ejercicio": ej, "fecha": ultima_fecha,
                                       "peso_max": 0.0, "reps_max": 0, "series": 0})
            d["series"] += 1
            d["peso_max"] = max(d["peso_max"], float(row.get("peso") or 0))
            d["reps_max"] = max(d["reps_max"], int(row.get("reps") or 0))
        return list(por_ej.values())
    except Exception as e:
        logger.error(f"Supabase get_ultima_sesion_resumen: {e}")
        return []


def deporte_hoy_tiene(actividad_substr: str) -> bool:
    """True si hoy ya hay una entrada en deporte cuya actividad contiene el texto dado."""
    try:
        r = get_client().table("deporte").select("actividad").eq("fecha", _today()).execute()
        return any(actividad_substr.lower() in str(row.get("actividad", "")).lower()
                   for row in (r.data or []))
    except Exception as e:
        logger.error(f"Supabase deporte_hoy_tiene: {e}")
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


def insert_ingreso(categoria, concepto, importe) -> bool:
    try:
        get_client().table("ingresos").insert({
            "fecha": _today(), "categoria": categoria,
            "concepto": concepto, "importe": float(importe),
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase insert_ingreso: {e}")
        return False


def get_balance_mes() -> dict:
    """Devuelve total gastos, total ingresos y balance del mes."""
    try:
        month_start = _today()[:8] + "01"
        g = get_client().table("gastos").select("importe").gte("fecha", month_start).execute()
        i = get_client().table("ingresos").select("importe").gte("fecha", month_start).execute()
        total_gastos = sum(float(r["importe"]) for r in (g.data or []))
        total_ingresos = sum(float(r["importe"]) for r in (i.data or []))
        return {"gastos": total_gastos, "ingresos": total_ingresos, "balance": total_ingresos - total_gastos}
    except Exception as e:
        logger.error(f"Supabase get_balance_mes: {e}")
        return {"gastos": 0, "ingresos": 0, "balance": 0}


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


def insert_habitos(ducha_fria, te_clavo, oracion, silencio, creatina=False) -> bool:
    try:
        get_client().table("habitos").insert({
            "fecha": _today(), "ducha_fria": ducha_fria, "te_clavo": te_clavo,
            "oracion": oracion, "silencio": silencio, "creatina": creatina,
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase insert_habitos: {e}")
        return False


# ── Perfil nutricional del usuario ──────────────────────────

def get_perfil() -> dict:
    try:
        r = get_client().table("perfil_usuario").select("*").eq("id", 1).execute()
        return r.data[0] if r.data else {}
    except Exception as e:
        logger.error(f"Supabase get_perfil: {e}")
        return {}


def save_perfil(data: dict) -> bool:
    try:
        payload = {k: v for k, v in data.items() if v is not None}
        payload["id"] = 1
        payload["updated_at"] = _today()
        get_client().table("perfil_usuario").upsert(payload).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase save_perfil: {e}")
        return False


def get_macros_hoy() -> dict:
    """Suma todos los registros de alimentación del día de hoy."""
    try:
        r = get_client().table("alimentacion").select("kcal,prot_g,carbs_g,grasas_g,agua_l").eq("fecha", _today()).execute()
        if not r.data:
            return {"kcal": 0, "prot_g": 0, "carbs_g": 0, "grasas_g": 0, "agua_l": 0}
        totals = {"kcal": 0.0, "prot_g": 0.0, "carbs_g": 0.0, "grasas_g": 0.0, "agua_l": 0.0}
        for row in r.data:
            for key in totals:
                totals[key] += float(row.get(key) or 0)
        return totals
    except Exception as e:
        logger.error(f"Supabase get_macros_hoy: {e}")
        return {"kcal": 0, "prot_g": 0, "carbs_g": 0, "grasas_g": 0, "agua_l": 0}


# ── Gastos del grupo familiar ────────────────────────────────

def insert_gasto_familia(miembro: str, concepto: str, importe: float, categoria: str, origen: str = "texto", fecha: str = None) -> bool:
    try:
        get_client().table("gastos_familia").insert({
            "fecha": fecha or _today(), "miembro": miembro, "concepto": concepto,
            "importe": float(importe), "categoria": categoria, "origen": origen,
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase insert_gasto_familia: {e}")
        return False


def insert_items_compra(items: list, fecha: str = None) -> bool:
    """Inserta lista de items extraídos de un ticket. items = [{"nombre","traduccion","cantidad","precio_unitario","total"}]"""
    try:
        rows = [
            {
                "fecha": fecha or _today(),
                "item_nombre": it.get("nombre", it.get("item_nombre", "")),
                "item_traduccion": it.get("traduccion", it.get("item_traduccion", "")),
                "cantidad": _num(it.get("cantidad")),
                "precio_unitario": _num(it.get("precio_unitario")),
                "total": _num(it.get("total")),
            }
            for it in items if it.get("nombre") or it.get("item_nombre")
        ]
        if rows:
            get_client().table("items_compra").insert(rows).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase insert_items_compra: {e}")
        return False


def delete_ultimo_gasto_familia() -> dict:
    """Borra el último registro de gastos_familia y sus items del mismo día. Devuelve lo borrado."""
    try:
        r = get_client().table("gastos_familia").select("*").order("created_at", desc=True).limit(1).execute()
        if not r.data:
            return {}
        row = r.data[0]
        get_client().table("gastos_familia").delete().eq("id", row["id"]).execute()
        get_client().table("items_compra").delete().eq("fecha", row["fecha"]).execute()
        return row
    except Exception as e:
        logger.error(f"Supabase delete_ultimo_gasto_familia: {e}")
        return {}


def delete_gastos_familia_mes() -> int:
    """Borra todos los gastos familiares e items del mes actual. Devuelve número de registros borrados."""
    try:
        month_start = _today()[:8] + "01"
        r = get_client().table("gastos_familia").delete().gte("fecha", month_start).execute()
        get_client().table("items_compra").delete().gte("fecha", month_start).execute()
        return len(r.data) if r.data else 0
    except Exception as e:
        logger.error(f"Supabase delete_gastos_familia_mes: {e}")
        return 0


def get_gastos_familia_mes() -> dict:
    """Total del mes + desglose por categoría."""
    try:
        month_start = _today()[:8] + "01"
        r = get_client().table("gastos_familia").select("categoria,importe,miembro,concepto,fecha").gte("fecha", month_start).execute()
        if not r.data:
            return {"total": 0.0, "por_categoria": {}, "registros": []}
        total = sum(float(row["importe"]) for row in r.data)
        por_cat: dict = {}
        for row in r.data:
            cat = row.get("categoria") or "Sin categoría"
            por_cat[cat] = por_cat.get(cat, 0.0) + float(row["importe"])
        return {"total": total, "por_categoria": por_cat, "registros": r.data}
    except Exception as e:
        logger.error(f"Supabase get_gastos_familia_mes: {e}")
        return {"total": 0.0, "por_categoria": {}, "registros": []}


def get_top_items_mes(n: int = 10) -> list:
    """Top N items más comprados este mes por gasto total."""
    try:
        month_start = _today()[:8] + "01"
        r = get_client().table("items_compra").select("item_nombre,total").gte("fecha", month_start).execute()
        if not r.data:
            return []
        totals: dict = {}
        for row in r.data:
            nombre = (row.get("item_nombre") or "").strip().title()
            totals[nombre] = totals.get(nombre, 0.0) + float(row.get("total") or 0)
        sorted_items = sorted(totals.items(), key=lambda x: x[1], reverse=True)
        return [{"item": k, "total": v} for k, v in sorted_items[:n]]
    except Exception as e:
        logger.error(f"Supabase get_top_items_mes: {e}")
        return []


# ── Datos curiosos (anti-repetición) ────────────────────────

def get_temas_recientes(n: int = 30) -> list:
    """Devuelve los últimos N títulos enviados para evitar repetición."""
    try:
        # Las marcas de los avisos de agenda (categoria='agenda') no son temas.
        r = get_client().table("datos_enviados").select("titulo") \
            .or_("categoria.is.null,categoria.neq.agenda") \
            .order("enviado_at", desc=True).limit(n).execute()
        return [row["titulo"] for row in (r.data or [])]
    except Exception as e:
        logger.error(f"Supabase get_temas_recientes: {e}")
        return []


def insert_dato_enviado(titulo: str, categoria: str, slot: str) -> bool:
    try:
        get_client().table("datos_enviados").insert({
            "titulo": titulo, "categoria": categoria, "slot": slot,
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase insert_dato_enviado: {e}")
        return False


# ── Mejoras de bienestar ─────────────────────────────────────

def get_mejoras(estado: str = None) -> list:
    try:
        sb = get_client()
        q = sb.table("mejoras").select("*").order("id")
        if estado:
            q = q.eq("estado", estado)
        r = q.execute()
        return r.data or []
    except Exception as e:
        logger.error(f"Supabase get_mejoras: {e}")
        return []


def insert_mejora(nombre: str, categoria: str, descripcion: str, evidencia: str) -> dict:
    try:
        r = get_client().table("mejoras").insert({
            "nombre": nombre, "categoria": categoria,
            "descripcion": descripcion, "evidencia": evidencia,
            "estado": "recomendada",
        }).execute()
        return r.data[0] if r.data else {}
    except Exception as e:
        logger.error(f"Supabase insert_mejora: {e}")
        return {}


def update_mejora_estado(mejora_id: int, nuevo_estado: str, fecha_inicio: str = None) -> bool:
    try:
        payload = {"estado": nuevo_estado}
        if fecha_inicio:
            payload["fecha_inicio"] = fecha_inicio
        get_client().table("mejoras").update(payload).eq("id", mejora_id).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase update_mejora_estado: {e}")
        return False


def get_recomendacion_semana() -> dict:
    """Devuelve una mejora aleatoria con estado 'recomendada'."""
    import random
    try:
        r = get_client().table("mejoras").select("*").eq("estado", "recomendada").execute()
        data = r.data or []
        return random.choice(data) if data else {}
    except Exception as e:
        logger.error(f"Supabase get_recomendacion_semana: {e}")
        return {}


# ── Ikigai resultado ─────────────────────────────────────────

def save_ikigai_resultado(analysis: dict, respuestas_raw: dict = None) -> bool:
    try:
        # Columnas jsonb: pasar listas/dicts NATIVOS (supabase-py los serializa a
        # jsonb correctamente). Antes se hacía _json.dumps() y se guardaban como
        # string → el dashboard veía un str en vez de lista y los círculos salían
        # en blanco. NO volver a envolver con json.dumps.
        payload = {
            "lo_que_amas":     analysis.get("lo_que_amas", []),
            "lo_que_bien":     analysis.get("lo_que_se_te_da_bien", []),
            "mundo_necesita":  analysis.get("lo_que_necesita_el_mundo", []),
            "te_pueden_pagar": analysis.get("por_lo_que_te_pueden_pagar", []),
            "ikigai_central":  analysis.get("ikigai_central", ""),
            "mision":          analysis.get("mision", ""),
            "vocacion":        analysis.get("vocacion", ""),
            "profesion":       analysis.get("profesion", ""),
            "pasion":          analysis.get("pasion", ""),
            "pasos_accion":    analysis.get("pasos_accion", []),
            "reflexion_final": analysis.get("reflexion_final", ""),
            "respuestas_raw":  respuestas_raw or {},
        }
        get_client().table("ikigai_resultado").insert(payload).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase save_ikigai_resultado: {e}")
        return False


def get_ikigai_ultimo() -> dict:
    """Devuelve el ikigai más reciente guardado."""
    try:
        import json as _json
        r = get_client().table("ikigai_resultado").select("*").order("id", desc=True).limit(1).execute()
        if not r.data:
            return {}
        row = r.data[0]
        # Deserializar los campos JSON
        for campo in ("lo_que_amas", "lo_que_bien", "mundo_necesita", "te_pueden_pagar", "pasos_accion", "respuestas_raw"):
            if isinstance(row.get(campo), str):
                try:
                    row[campo] = _json.loads(row[campo])
                except Exception:
                    pass
        return row
    except Exception as e:
        logger.error(f"Supabase get_ikigai_ultimo: {e}")
        return {}


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

        mejoras_activas = get_mejoras("activa") + get_mejoras("en_proceso")
        mejoras_str = ", ".join(m["nombre"] for m in mejoras_activas) or "ninguna"

        return (
            f"MES: {_today()[:7]}\n"
            f"STREAKS: ducha_fria={streak('ducha_fria')}d | te_clavo={streak('te_clavo')}d | "
            f"oracion={streak('oracion')}d | creatina={streak('creatina')}d\n"
            f"ENTRENOS_MES: {dep_r.count or 0}\n"
            f"DIETA_DIAS_MES: {alim_r.count or 0}\n"
            f"GASTOS_MES: €{total_gastos:.2f}\n"
            f"TARGET_NUTRI: 2800kcal|150gProt|350gCarbs|78gGrasas|3L agua\n"
            f"OBJETIVO: ganar masa muscular\n"
            f"MEJORAS_ACTIVAS: {mejoras_str}\n"
            f"XP_TOTAL: {xp.get('total_xp', 0)}"
        )
    except Exception as e:
        logger.error(f"Supabase get_vida_state_summary: {e}")
        return "Estado no disponible temporalmente."


# ── Rutina semanal de gym ────────────────────────────────────

def get_rutina_dia(dia_semana: int) -> dict:
    """Rutina de un día (0=Lunes .. 6=Domingo). {} si no hay nada definido."""
    try:
        r = get_client().table("rutina_gym").select("*").eq("dia_semana", dia_semana).limit(1).execute()
        return r.data[0] if r.data else {}
    except Exception as e:
        logger.error(f"Supabase get_rutina_dia: {e}")
        return {}


def get_rutina_completa() -> list:
    """Toda la rutina semanal, ordenada por día."""
    try:
        r = get_client().table("rutina_gym").select("*").order("dia_semana").execute()
        return r.data or []
    except Exception as e:
        logger.error(f"Supabase get_rutina_completa: {e}")
        return []


# ── Memoria conversacional ───────────────────────────────────

def guardar_mensaje(chat_id: str, rol: str, contenido: str) -> bool:
    """Guarda un turno de conversación (rol: 'user' | 'assistant')."""
    try:
        if not contenido or not contenido.strip():
            return False
        get_client().table("conversaciones").insert({
            "chat_id": str(chat_id), "rol": rol, "contenido": contenido[:4000],
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase guardar_mensaje: {e}")
        return False


def get_historial_chat(chat_id: str, n: int = 10) -> list:
    """Últimos n turnos de un chat en orden cronológico (antiguo → reciente),
    como [{'role': 'user'|'assistant', 'content': str}] listo para Groq."""
    try:
        r = get_client().table("conversaciones").select("rol,contenido") \
            .eq("chat_id", str(chat_id)).order("created_at", desc=True).limit(n).execute()
        filas = list(reversed(r.data or []))
        return [{"role": f.get("rol", "user"), "content": f.get("contenido", "")} for f in filas]
    except Exception as e:
        logger.error(f"Supabase get_historial_chat: {e}")
        return []


def limpiar_historial_chat(chat_id: str, conservar: int = 40) -> None:
    """Borra mensajes antiguos del chat, conservando los `conservar` más recientes."""
    try:
        r = get_client().table("conversaciones").select("id") \
            .eq("chat_id", str(chat_id)).order("created_at", desc=True).limit(500).execute()
        ids = [row["id"] for row in (r.data or [])][conservar:]
        if ids:
            get_client().table("conversaciones").delete().in_("id", ids).execute()
    except Exception as e:
        logger.error(f"Supabase limpiar_historial_chat: {e}")


# ── Módulo Descanso/Sueño ────────────────────────────────────

def get_sueno_config() -> dict:
    """Configuración del módulo de sueño (fila singleton id=1)."""
    try:
        r = get_client().table("sueno_config").select("*").eq("id", 1).limit(1).execute()
        return (r.data or [{}])[0]
    except Exception as e:
        logger.error(f"Supabase get_sueno_config: {e}")
        return {}


def get_comandos_pendientes() -> list:
    """Órdenes de luces pendientes de ejecutar (más antiguas primero)."""
    try:
        r = get_client().table("luces_comando").select("*") \
            .eq("estado", "pendiente").order("created_at", desc=False).limit(50).execute()
        return r.data or []
    except Exception as e:
        logger.error(f"Supabase get_comandos_pendientes: {e}")
        return []


def marcar_comando(comando_id, estado: str = "hecho") -> None:
    """Marca una orden de luz como hecha/error."""
    try:
        get_client().table("luces_comando").update({"estado": estado}) \
            .eq("id", int(comando_id)).execute()
    except Exception as e:
        logger.error(f"Supabase marcar_comando: {e}")


def insert_sueno_registro(hora_dormir, hora_despertar, horas, ciclos,
                          calidad=None, notas="") -> bool:
    """Registra una noche de sueño (usado opcionalmente desde el bot)."""
    try:
        get_client().table("sueno_registros").insert({
            "fecha": _today(),
            "hora_dormir": hora_dormir,
            "hora_despertar": hora_despertar,
            "horas": horas,
            "ciclos": ciclos,
            "calidad": calidad,
            "notas": notas,
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Supabase insert_sueno_registro: {e}")
        return False


# ── Agenda (tabla agenda_eventos, la misma que pinta la PWA) ──

_AGENDA_TIPOS = ("entreno", "descanso", "personal", "trabajo", "salud", "finanzas", "otro")
_AGENDA_ESTADOS = ("planificado", "hecho", "saltado", "movido")


def _agenda_fecha(v) -> str:
    """Fecha ISO válida o ''."""
    try:
        return date.fromisoformat(str(v or "")[:10]).isoformat()
    except ValueError:
        return ""


def _agenda_hora(v):
    """'HH:MM' válida o None (evento de todo el día)."""
    s = str(v or "").strip()[:5]
    ok = len(s) == 5 and s[2] == ":" and s[:2].isdigit() and s[3:].isdigit() \
        and int(s[:2]) < 24 and int(s[3:]) < 60
    return s if ok else None


def _agenda_fila(e: dict) -> dict:
    """Fila completa y saneada para insertar (sin user_id: lo pone agenda_insertar)."""
    return {
        "fecha": _agenda_fecha(e.get("fecha")),
        "hora": _agenda_hora(e.get("hora")),
        "titulo": str(e.get("titulo") or "").strip()[:200] or None,
        "tipo": e.get("tipo") if e.get("tipo") in _AGENDA_TIPOS else "otro",
        "estado": e.get("estado") if e.get("estado") in _AGENDA_ESTADOS else "planificado",
        "notas": str(e.get("notas") or "").strip()[:1000] or None,
        "color": str(e.get("color") or "").strip()[:20] or None,
    }


_agenda_dueno_id = None


def agenda_dueno():
    """user_id del dueño: el de la fila 1 de perfil_usuario, que el script
    multiusuario dejó a su nombre. Con la service_role no hay RLS, así que los
    eventos de otra persona registrada en la PWA se filtran con esto.
    None si no se pudo averiguar (y entonces la agenda no hace nada)."""
    global _agenda_dueno_id
    if _agenda_dueno_id is None:
        try:
            r = get_client().table("perfil_usuario").select("user_id").eq("id", 1).limit(1).execute()
            _agenda_dueno_id = (r.data or [{}])[0].get("user_id") or None
        except Exception as e:
            _rutina_fallo("agenda_dueno", e)     # como mucho una vez por hora: se consulta en cada pasada
    return _agenda_dueno_id


def agenda_listar(desde: str, hasta: str = None) -> list:
    """Eventos del dueño entre dos fechas ISO (ambas incluidas)."""
    desde = _agenda_fecha(desde)
    hasta = _agenda_fecha(hasta) or desde
    dueno = agenda_dueno()
    if not desde or not dueno:
        return []
    try:
        r = get_client().table("agenda_eventos").select("*").eq("user_id", dueno) \
            .gte("fecha", desde).lte("fecha", hasta) \
            .order("fecha", desc=False).limit(500).execute()
        return r.data or []
    except Exception as e:
        logger.error(f"Supabase agenda_listar: {e}")
        return []


def agenda_insertar(eventos: list) -> list:
    """Inserta eventos en la agenda. Devuelve las filas creadas (con su id)."""
    if not isinstance(eventos, list):
        return []
    dueno = agenda_dueno()
    filas = [dict(f, user_id=dueno) for f in (_agenda_fila(e) for e in eventos[:200]
                                              if isinstance(e, dict)) if f["fecha"]]
    if not filas or not dueno:
        return []
    try:
        r = get_client().table("agenda_eventos").insert(filas).execute()
        return r.data or []
    except Exception as e:
        logger.error(f"Supabase agenda_insertar: {e}")
        return []


def agenda_actualizar(evento_id, cambios: dict) -> bool:
    """Cambia campos de un evento (los mismos que acepta agenda_insertar)."""
    cambios = cambios if isinstance(cambios, dict) else {}
    limpio = _agenda_fila(cambios)
    datos = {k: limpio[k] for k in limpio if k in cambios}
    dueno = agenda_dueno()
    if not datos or ("fecha" in datos and not datos["fecha"]) or not dueno:
        return False
    try:
        r = get_client().table("agenda_eventos").update(datos) \
            .eq("id", int(evento_id)).eq("user_id", dueno).execute()
        return bool(r.data)
    except Exception as e:
        logger.error(f"Supabase agenda_actualizar: {e}")
        return False


def agenda_borrar(ids: list) -> int:
    """Borra eventos por id. Devuelve cuántos se borraron."""
    try:
        if not isinstance(ids, list):
            return 0
        ids = [int(i) for i in ids[:200]]
        dueno = agenda_dueno()
        if not ids or not dueno:
            return 0
        r = get_client().table("agenda_eventos").delete() \
            .in_("id", ids).eq("user_id", dueno).execute()
        return len(r.data or [])
    except Exception as e:
        logger.error(f"Supabase agenda_borrar: {e}")
        return 0


def aviso_agenda_enviado(clave: str):
    """True/False si el aviso de agenda `clave` ya se mandó; None si no se pudo
    comprobar. La marca vive en datos_enviados con categoria='agenda'."""
    try:
        r = get_client().table("datos_enviados").select("id") \
            .eq("categoria", "agenda").eq("titulo", clave).limit(1).execute()
        return bool(r.data)
    except Exception as e:
        logger.error(f"Supabase aviso_agenda_enviado: {e}")
        return None

# ── Mi día (tablas rutina_bloques / rutina_dias, las mismas que escribe la PWA) ──
# Reglas comunes con la app (todas obligatorias):
#  1. Las filas de un día se crean «insertando e ignorando duplicados»: nunca se pisa una fila.
#  2. Los cambios van por id (y por dueño) y solo con las columnas que cambian.
#  3. Solo el bot escribe avisado_at, aviso_msg_id y pregunta_msg_id.
#  4. Si cambia o se desactiva un bloque, se borran sus filas futuras sin tocar (planificadas, sin
#     avisar, de la plantilla y nunca editadas: updated_at == created_at) y se rehacen las de hoy
#     y mañana.
#  5. Con la service_role no hay RLS: TODA lectura filtra por dueño y TODA inserción lo pone.
# Todas las funciones devuelven un valor seguro si algo falla y registran el error como mucho
# una vez por hora y función (el planificador pasa cada minuto: no debe llenar el registro).

_RUTINA_AREAS = ("alma", "mente", "cuerpo", "negocio", "trabajo", "vida")
_RUTINA_CLAVES = ("gym", "repaso", "oracion")
_RUTINA_ESTADOS = ("planificado", "hecho", "minimo", "no", "movido")
_RUTINA_ORIGENES = ("plantilla", "manual", "rescate", "hueco")
_RUTINA_COPIA = ("titulo", "emoji", "area", "hora", "duracion_min", "minimo_min", "minimo_txt",
                 "enlace", "aviso", "aviso_antes_min", "estudio", "clave")
_HABITOS_COLS = ("ducha_fria", "te_clavo", "oracion", "silencio", "creatina")
_RUTINA_HORA_RE = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
_RUTINA_MAX_DIAS = 62
_rutina_err_log = {}


def _rutina_fallo(op: str, e):
    """Registra un error de la rutina como mucho una vez por hora y operación."""
    ahora = time.time()
    if ahora - _rutina_err_log.get(op, 0) >= 3600:
        _rutina_err_log[op] = ahora
        logger.error(f"Supabase {op}: {e}")


def _rutina_hora(v):
    """'HH:MM' válida (acepta 'HH:MM:SS') o None."""
    s = str(v or "").strip()
    if len(s) == 8 and s[5] == ":":
        s = s[:5]
    return s if _RUTINA_HORA_RE.fullmatch(s) else None


def _rutina_entero(v, lo: int, hi: int):
    """Entero dentro de [lo, hi] o None (los booleanos y los decimales no cuentan)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        if isinstance(v, float):
            if v != int(v):
                return None
            n = int(v)
        else:
            n = int(str(v).strip())
    except (ValueError, TypeError, OverflowError):
        return None
    return n if lo <= n <= hi else None


def _rutina_bool(v):
    """True/False, o None si no es un booleano reconocible."""
    if isinstance(v, bool):
        return v
    if v in (0, 1) and not isinstance(v, str):
        return bool(v)
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    return None


def _rutina_fecha_dia(v):
    """date de una fecha ISO (o date) o None."""
    s = _agenda_fecha(v)
    return date.fromisoformat(s) if s else None


def _rutina_ahora_madrid() -> datetime:
    """Hora local de España (sin zona) para quien no recibe `ahora` de fuera."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Madrid")).replace(tzinfo=None)
    except Exception:
        return datetime.now()


def _rutina_sanear_bloque(e, completo: bool = True):
    """Campos válidos de un bloque de la plantilla, o None si algún valor es inválido.
    completo=True (alta): los que faltan toman su valor por defecto y titulo/hora son obligatorios.
    completo=False (cambio): solo salen los campos que venían. Nunca pasan id, user_id ni
    columnas que no sean de la plantilla."""
    if not isinstance(e, dict):
        return None
    out = {}
    if completo or "titulo" in e:
        titulo = str(e.get("titulo") or "").strip()[:200]
        if not titulo:
            return None
        out["titulo"] = titulo
    if completo or "hora" in e:
        hora = _rutina_hora(e.get("hora"))
        if not hora:
            return None
        out["hora"] = hora
    if completo or "dias" in e:
        bruto = e.get("dias", [0, 1, 2, 3, 4, 5, 6])
        if not isinstance(bruto, (list, tuple)):
            return None
        dias = [_rutina_entero(d, 0, 6) for d in bruto]
        if not dias or any(d is None for d in dias):
            return None
        out["dias"] = sorted(set(dias))
    if completo or "area" in e:
        area = e.get("area") if e.get("area") is not None else "mente"
        if area not in _RUTINA_AREAS:
            return None
        out["area"] = area
    if completo or "emoji" in e:
        out["emoji"] = str(e.get("emoji") or "").strip()[:16] or None
    if completo or "duracion_min" in e:
        dur = _rutina_entero(30 if e.get("duracion_min") is None else e.get("duracion_min"), 1, 720)
        if dur is None:
            return None
        out["duracion_min"] = dur
    if completo or "minimo_min" in e:
        bruto = e.get("minimo_min")
        mini = None if bruto in (None, "") else _rutina_entero(bruto, 1, 720)
        if bruto not in (None, "") and mini is None:
            return None
        out["minimo_min"] = mini
    if completo or "minimo_txt" in e:
        out["minimo_txt"] = str(e.get("minimo_txt") or "").strip()[:200] or None
    if completo or "enlace" in e:
        enlace = str(e.get("enlace") or "").strip()[:500]
        ok = enlace.startswith(("http://", "https://")) or (enlace.startswith("/") and not enlace.startswith("//"))
        out["enlace"] = enlace if enlace and ok else None
    if completo or "aviso" in e:
        aviso = True if e.get("aviso") is None else _rutina_bool(e.get("aviso"))
        if aviso is None:
            return None
        out["aviso"] = aviso
    if completo or "aviso_antes_min" in e:
        antes = _rutina_entero(0 if e.get("aviso_antes_min") is None else e.get("aviso_antes_min"), 0, 240)
        if antes is None:
            return None
        out["aviso_antes_min"] = antes
    if completo or "estudio" in e:
        estudio = False if e.get("estudio") is None else _rutina_bool(e.get("estudio"))
        if estudio is None:
            return None
        out["estudio"] = estudio
    if completo or "clave" in e:
        clave = e.get("clave") or None
        if clave is not None and clave not in _RUTINA_CLAVES:
            return None
        out["clave"] = clave
    if completo or "orden" in e:
        orden = _rutina_entero(0 if e.get("orden") is None else e.get("orden"), 0, 100000)
        if orden is None:
            return None
        out["orden"] = orden
    if "activo" in e:
        activo = _rutina_bool(e.get("activo"))
        if activo is None:
            return None
        out["activo"] = activo
    return out


def _rutina_bloques_a_filas(bloques: list, desde: date, hasta: date, dueno) -> list:
    """Filas de día (sin id ni columnas del bot) de cada bloque en cada fecha de [desde, hasta]
    cuyo día de la semana (0 = lunes) esté en `dias`. Todas con las mismas claves."""
    filas = []
    dia = desde
    while dia <= hasta:
        for b in bloques:
            if dia.weekday() not in (b.get("dias") or []):
                continue
            fila = {k: b.get(k) for k in _RUTINA_COPIA}
            fila.update({"user_id": dueno, "fecha": dia.isoformat(), "bloque_id": b["id"],
                         "origen_bloque_id": b["id"], "estado": "planificado", "origen": "plantilla"})
            filas.append(fila)
        dia += timedelta(days=1)
    return filas


def _rutina_insertar_ignorando(filas: list) -> None:
    """Regla 1: insertar e ignorar los duplicados (user_id, fecha, bloque_id)."""
    for i in range(0, len(filas), 100):
        get_client().table("rutina_dias").upsert(
            filas[i:i + 100], on_conflict="user_id,fecha,bloque_id", ignore_duplicates=True).execute()


def rutina_bloques_activos() -> list:
    """Bloques activos de la plantilla del dueño."""
    dueno = agenda_dueno()
    if not dueno:
        return []
    try:
        r = get_client().table("rutina_bloques").select("*").eq("user_id", dueno) \
            .eq("activo", True).order("orden", desc=False).order("hora", desc=False).limit(500).execute()
        return r.data or []
    except Exception as e:
        _rutina_fallo("rutina_bloques_activos", e)
        return []


def rutina_materializar(desde, hasta) -> int:
    """Crea las filas de cada día entre `desde` y `hasta` (ambos incluidos, máx. 62 días) para los
    bloques activos cuyo día de la semana toque. Idempotente (regla 1). Devuelve cuántas filas
    candidatas había (las que ya existían se ignoran en la base de datos)."""
    d, h = _rutina_fecha_dia(desde), _rutina_fecha_dia(hasta)
    dueno = agenda_dueno()
    if not d or not h or not dueno:
        return 0
    h = min(max(h, d), d + timedelta(days=_RUTINA_MAX_DIAS - 1))
    try:
        bloques = rutina_bloques_activos()
        filas = _rutina_bloques_a_filas(bloques, d, h, dueno)
        if filas:
            _rutina_insertar_ignorando(filas)
        return len(filas)
    except Exception as e:
        _rutina_fallo("rutina_materializar", e)
        return 0


def rutina_del_dia(fecha) -> list:
    """Filas de rutina_dias del dueño en una fecha, por hora."""
    f = _agenda_fecha(fecha)
    dueno = agenda_dueno()
    if not f or not dueno:
        return []
    try:
        r = get_client().table("rutina_dias").select("*").eq("user_id", dueno).eq("fecha", f) \
            .order("hora", desc=False).order("created_at", desc=False).limit(500).execute()
        return r.data or []
    except Exception as e:
        _rutina_fallo("rutina_del_dia", e)
        return []


def _rutina_uuid(v):
    """uuid en texto canónico o None."""
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, AttributeError, TypeError):
        return None


def rutina_fila(fila_id):
    """Una fila de rutina_dias del dueño por su id, o None."""
    uid = _rutina_uuid(fila_id)
    dueno = agenda_dueno()
    if not uid or not dueno:
        return None
    try:
        r = get_client().table("rutina_dias").select("*").eq("id", uid).eq("user_id", dueno) \
            .limit(1).execute()
        return (r.data or [None])[0]
    except Exception as e:
        _rutina_fallo("rutina_fila", e)
        return None


def rutina_por_mensaje(msg_id):
    """La fila del dueño cuyo aviso_msg_id o pregunta_msg_id es ese mensaje de Telegram, o None."""
    if isinstance(msg_id, bool) or not isinstance(msg_id, int):
        return None
    dueno = agenda_dueno()
    if not dueno:
        return None
    try:
        r = get_client().table("rutina_dias").select("*").eq("user_id", dueno) \
            .or_(f"aviso_msg_id.eq.{msg_id},pregunta_msg_id.eq.{msg_id}") \
            .order("created_at", desc=True).limit(1).execute()
        return (r.data or [None])[0]
    except Exception as e:
        _rutina_fallo("rutina_por_mensaje", e)
        return None


def rutina_reclamar_aviso(fila_id) -> bool:
    """Reclamo atómico del aviso: pone avisado_at solo si seguía vacío Y la fila sigue planificada
    (entre leerla y reclamarla la app pudo darla por hecha: entonces no debe avisar). True
    únicamente si esta llamada fue la que lo puso (una fila vuelve en la respuesta): solo un
    proceso puede enviar."""
    uid = _rutina_uuid(fila_id)
    dueno = agenda_dueno()
    if not uid or not dueno:
        return False
    try:
        r = get_client().table("rutina_dias").update(
            {"avisado_at": datetime.now(timezone.utc).isoformat()}) \
            .eq("id", uid).eq("user_id", dueno).eq("estado", "planificado") \
            .is_("avisado_at", "null").execute()
        return bool(r.data)
    except Exception as e:
        _rutina_fallo("rutina_reclamar_aviso", e)
        return False


def rutina_guardar_msg(fila_id, campo: str, msg_id) -> bool:
    """Guarda aviso_msg_id o pregunta_msg_id (solo los escribe el bot)."""
    uid = _rutina_uuid(fila_id)
    dueno = agenda_dueno()
    if campo not in ("aviso_msg_id", "pregunta_msg_id") or not uid or not dueno \
            or isinstance(msg_id, bool) or not isinstance(msg_id, int):
        return False
    try:
        r = get_client().table("rutina_dias").update({campo: msg_id}) \
            .eq("id", uid).eq("user_id", dueno).execute()
        return bool(r.data)
    except Exception as e:
        _rutina_fallo("rutina_guardar_msg", e)
        return False


def rutina_liberar_aviso(fila_id) -> bool:
    """Suelta el reclamo (avisado_at y aviso_msg_id a nulo): el siguiente minuto vuelve a avisar."""
    uid = _rutina_uuid(fila_id)
    dueno = agenda_dueno()
    if not uid or not dueno:
        return False
    try:
        r = get_client().table("rutina_dias").update({"avisado_at": None, "aviso_msg_id": None}) \
            .eq("id", uid).eq("user_id", dueno).execute()
        return bool(r.data)
    except Exception as e:
        _rutina_fallo("rutina_liberar_aviso", e)
        return False


def rutina_aplazar(fila_id, hora) -> bool:
    """Aplaza un aviso en UNA sola escritura: la hora nueva y el reclamo suelto (avisado_at y
    aviso_msg_id a nulo), solo si la fila del dueño sigue planificada. True si cambió una fila:
    la hora y el reclamo nunca quedan a medias ni se aplaza algo que la app ya dio por resuelto."""
    uid = _rutina_uuid(fila_id)
    nueva = _rutina_hora(hora)
    dueno = agenda_dueno()
    if not uid or not nueva or not dueno:
        return False
    try:
        r = get_client().table("rutina_dias").update(
            {"hora": nueva, "avisado_at": None, "aviso_msg_id": None}) \
            .eq("id", uid).eq("user_id", dueno).eq("estado", "planificado").execute()
        return bool(r.data)
    except Exception as e:
        _rutina_fallo("rutina_aplazar", e)
        return False


def rutina_actualizar(fila_id, cambios: dict) -> bool:
    """Cambia una fila del día por id y dueño, solo con columnas permitidas:
    estado, hora, nota, aprendido y minutos_reales."""
    uid = _rutina_uuid(fila_id)
    dueno = agenda_dueno()
    if not uid or not dueno or not isinstance(cambios, dict):
        return False
    datos = {}
    if "estado" in cambios:
        if cambios["estado"] not in _RUTINA_ESTADOS:
            return False
        datos["estado"] = cambios["estado"]
    if "hora" in cambios:
        hora = _rutina_hora(cambios["hora"])
        if not hora:
            return False
        datos["hora"] = hora
    for campo in ("nota", "aprendido"):
        if campo in cambios:
            datos[campo] = str(cambios[campo] or "").strip()[:4000] or None
    if "minutos_reales" in cambios:
        mins = None if cambios["minutos_reales"] is None else _rutina_entero(cambios["minutos_reales"], 0, 1440)
        if cambios["minutos_reales"] is not None and mins is None:
            return False
        datos["minutos_reales"] = mins
    if not datos:
        return False
    try:
        r = get_client().table("rutina_dias").update(datos).eq("id", uid).eq("user_id", dueno).execute()
        return bool(r.data)
    except Exception as e:
        _rutina_fallo("rutina_actualizar", e)
        return False


def rutina_dia_insertar(fila: dict):
    """Inserta una fila suelta en un día (bloque_id nulo: no choca con nada). Devuelve la fila
    creada (con su id) o None. Campos: los del bloque + fecha, origen y estado."""
    dueno = agenda_dueno()
    datos = _rutina_sanear_bloque(fila, completo=True) if isinstance(fila, dict) else None
    fecha = _agenda_fecha(fila.get("fecha")) if isinstance(fila, dict) else ""
    if not dueno or datos is None or not fecha:
        return None
    nueva = {k: datos[k] for k in _RUTINA_COPIA}
    origen = fila.get("origen") if fila.get("origen") in _RUTINA_ORIGENES else "manual"
    nueva.update({"user_id": dueno, "fecha": fecha, "bloque_id": None, "origen_bloque_id": None,
                  "estado": "planificado", "origen": origen})
    try:
        r = get_client().table("rutina_dias").insert(nueva).execute()
        return (r.data or [None])[0]
    except Exception as e:
        _rutina_fallo("rutina_dia_insertar", e)
        return None


_RUTINA_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?\s*(Z|[+-]\d{2}(?::?\d{2})?)?")


def _rutina_instante(v):
    """Marca de tiempo de Postgres ('2026-11-09T08:00:17.1234+00:00', con 'Z', con 0 a 9 cifras de
    fracción) como datetime en UTC sin zona, o None si no se entiende. A mano porque
    datetime.fromisoformat de Python 3.9 rechaza 'Z' y las fracciones de 1, 2, 4 o 5 cifras."""
    m = _RUTINA_TS_RE.fullmatch(str(v or "").strip())
    if not m:
        return None
    dia, hh, mm, ss, frac, zona = m.groups()
    try:
        t = datetime.strptime(f"{dia} {hh}:{mm}:{ss}", "%Y-%m-%d %H:%M:%S") \
            .replace(microsecond=int((frac or "0")[:6].ljust(6, "0")))
        if zona and zona != "Z":
            cifras = zona[1:].replace(":", "")
            t -= (-1 if zona[0] == "-" else 1) * timedelta(hours=int(cifras[:2]), minutes=int(cifras[2:4] or 0))
        return t
    except (ValueError, OverflowError):
        return None


def _rutina_sin_editar(fila: dict) -> bool:
    """True solo si nadie editó nunca la fila (updated_at == created_at: el disparador de la base
    sube updated_at en cada cambio). Si falta uno de los dos o no se entiende, cuenta como editada."""
    c, u = _rutina_instante(fila.get("created_at")), _rutina_instante(fila.get("updated_at"))
    return c is not None and u is not None and c == u


def rutina_rehacer_bloque(bloque_id, hoy, ahora_hhmm) -> dict:
    """Regla 4 (igual que la app): tras cambiar o desactivar un bloque, borra sus filas futuras
    sin tocar (planificado, sin avisar, de la plantilla, con fecha > hoy o de hoy con hora > ahora
    Y nunca editadas: updated_at == created_at, así una nota, un cambio de hora o un «+30 min» se
    conservan) y recrea las de hoy y mañana si el bloque sigue activo. PostgREST no compara dos
    columnas entre sí: se eligen los candidatos en la base y lo de «nunca editada» se mira aquí.
    Devuelve {'borradas': n, 'creadas': n}."""
    res = {"borradas": 0, "creadas": 0}
    dueno = agenda_dueno()
    d = _rutina_fecha_dia(hoy)
    hhmm = _rutina_hora(ahora_hhmm)
    bid = _rutina_entero(bloque_id, 1, 2 ** 62)
    if not dueno or not d or not hhmm or bid is None:
        return res
    try:
        c = get_client()
        hoy_iso = d.isoformat()
        candidatas = c.table("rutina_dias").select("id,fecha,hora,created_at,updated_at") \
            .eq("user_id", dueno).eq("bloque_id", bid).gte("fecha", hoy_iso) \
            .eq("estado", "planificado").eq("origen", "plantilla").is_("avisado_at", "null") \
            .limit(1000).execute().data or []
        ids = []
        for f in candidatas:
            fecha, hora, fid = str(f.get("fecha") or "")[:10], _rutina_hora(f.get("hora")), _rutina_uuid(f.get("id"))
            futura = fecha > hoy_iso or (fecha == hoy_iso and hora is not None and hora > hhmm)
            if fid and futura and _rutina_sin_editar(f):
                ids.append(fid)
        for i in range(0, len(ids), 100):
            # se repiten estado y avisado_at: si el bot avisó o la app la resolvió desde la consulta, no se borra
            borradas = c.table("rutina_dias").delete().in_("id", ids[i:i + 100]).eq("user_id", dueno) \
                .eq("estado", "planificado").is_("avisado_at", "null").execute()
            res["borradas"] += len(borradas.data or [])
        r = get_client().table("rutina_bloques").select("*").eq("id", bid).eq("user_id", dueno) \
            .eq("activo", True).limit(1).execute()
        if r.data:
            filas = _rutina_bloques_a_filas(r.data, d, d + timedelta(days=1), dueno)
            if filas:
                _rutina_insertar_ignorando(filas)
            res["creadas"] = len(filas)
    except Exception as e:
        _rutina_fallo("rutina_rehacer_bloque", e)
    return res


def rutina_listar(desde, hasta=None) -> dict:
    """Plantilla (todos los bloques, también los desactivados), filas de los días y diario del
    dueño entre dos fechas (máx. 62 días). Cada consulta pide 1000 filas como mucho: es el tope de
    PostgREST (más no devuelve), y 62 días de 16 bloques caben justos."""
    vacio = {"bloques": [], "dias": [], "diario": []}
    d = _rutina_fecha_dia(desde)
    h = _rutina_fecha_dia(hasta) or d
    dueno = agenda_dueno()
    if not d or not dueno:
        return vacio
    h = min(max(h, d), d + timedelta(days=_RUTINA_MAX_DIAS - 1))
    try:
        c = get_client()
        bloques = c.table("rutina_bloques").select("*").eq("user_id", dueno) \
            .order("orden", desc=False).order("id", desc=False).limit(500).execute().data or []
        dias = c.table("rutina_dias").select("*").eq("user_id", dueno) \
            .gte("fecha", d.isoformat()).lte("fecha", h.isoformat()) \
            .order("fecha", desc=False).order("hora", desc=False).limit(1000).execute().data or []
        diario = c.table("diario").select("*").eq("user_id", dueno) \
            .gte("fecha", d.isoformat()).lte("fecha", h.isoformat()) \
            .order("fecha", desc=False).order("created_at", desc=False).limit(500).execute().data or []
        return {"bloques": bloques, "dias": dias, "diario": diario}
    except Exception as e:
        _rutina_fallo("rutina_listar", e)
        return vacio


def rutina_bloque_insertar(lista, ahora: datetime = None) -> list:
    """Da de alta bloques de la plantilla (los inválidos se descartan) y crea ya sus filas de hoy
    y mañana. Devuelve los bloques creados (con su id)."""
    if not isinstance(lista, list):
        return []
    dueno = agenda_dueno()
    filas = []
    for e in lista[:100]:
        datos = _rutina_sanear_bloque(e, completo=True)
        if datos is not None:
            datos.setdefault("activo", True)
            filas.append(dict(datos, user_id=dueno))
    if not filas or not dueno:
        return []
    try:
        r = get_client().table("rutina_bloques").insert(filas).execute()
        creados = r.data or []
        hoy = (ahora or _rutina_ahora_madrid()).date()
        nuevas = _rutina_bloques_a_filas([b for b in creados if b.get("activo", True)],
                                         hoy, hoy + timedelta(days=1), dueno)
        if nuevas:
            _rutina_insertar_ignorando(nuevas)
        return creados
    except Exception as e:
        _rutina_fallo("rutina_bloque_insertar", e)
        return []


def rutina_bloque_actualizar(bloque_id, cambios: dict, ahora: datetime = None) -> bool:
    """Cambia campos de un bloque por id y dueño; después aplica la regla 4 (rehace sus filas)."""
    bid = _rutina_entero(bloque_id, 1, 2 ** 62)
    dueno = agenda_dueno()
    datos = _rutina_sanear_bloque(cambios, completo=False)
    if bid is None or not dueno or not datos:
        return False
    try:
        r = get_client().table("rutina_bloques").update(datos).eq("id", bid).eq("user_id", dueno).execute()
        if not r.data:
            return False
        ahora = ahora or _rutina_ahora_madrid()
        rutina_rehacer_bloque(bid, ahora.date(), ahora.strftime("%H:%M"))
        return True
    except Exception as e:
        _rutina_fallo("rutina_bloque_actualizar", e)
        return False


def rutina_bloque_desactivar(bloque_id, ahora: datetime = None) -> bool:
    """Desactiva un bloque (nunca se borra) y retira sus filas futuras sin tocar."""
    return rutina_bloque_actualizar(bloque_id, {"activo": False}, ahora)


# ── Diario, hábitos y entreno (lo que la rutina escribe o consulta) ──

def anexar_texto(viejo, nuevo: str, tope: int = 4000) -> str:
    """Texto final al añadir `nuevo` a `viejo`: solo si viejo está vacío, ' · ' + nuevo si no
    lo contenía ya."""
    viejo = str(viejo or "").strip()
    if not viejo:
        return nuevo
    if nuevo in viejo:
        return viejo
    return (viejo + " · " + nuevo)[:tope]


def diario_guardar(fecha, mejora=None, lo_importante=None) -> bool:
    """Guarda en el diario de `fecha` (la de la fila, no la de hoy). Si ya hay fila ese día
    (la más reciente) se actualiza, y un texto ya escrito no se pisa: se le añade ' · ' + lo
    nuevo. Si no hay fila, se crea una."""
    f = _agenda_fecha(fecha)
    dueno = agenda_dueno()
    nuevos = {k: str(v).strip()[:1000] for k, v in (("mejora", mejora), ("lo_importante", lo_importante))
              if v is not None and str(v).strip()}
    if not f or not dueno or not nuevos:
        return False
    try:
        c = get_client()
        r = c.table("diario").select("*").eq("user_id", dueno).eq("fecha", f) \
            .order("created_at", desc=True).limit(1).execute()
        if r.data:
            fila = r.data[0]
            cambios = {}
            for k, nuevo in nuevos.items():
                final = anexar_texto(fila.get(k), nuevo)
                if final != str(fila.get(k) or "").strip():
                    cambios[k] = final
            if cambios:
                c.table("diario").update(cambios).eq("id", fila["id"]).eq("user_id", dueno).execute()
            return True
        c.table("diario").insert(dict(nuevos, fecha=f, user_id=dueno)).execute()
        return True
    except Exception as e:
        _rutina_fallo("diario_guardar", e)
        return False


def habito_marcar(fecha, clave: str) -> bool:
    """Marca un hábito (ducha_fria, te_clavo, oracion, silencio, creatina) como hecho en `fecha`:
    actualiza todas las filas del dueño de ese día (la app y el bot pueden haber creado varias)
    y, si no había ninguna, crea una."""
    f = _agenda_fecha(fecha)
    dueno = agenda_dueno()
    if clave not in _HABITOS_COLS or not f or not dueno:
        return False
    try:
        c = get_client()
        r = c.table("habitos").update({clave: True}).eq("user_id", dueno).eq("fecha", f).execute()
        if not r.data:
            c.table("habitos").insert({"user_id": dueno, "fecha": f, clave: True}).execute()
        return True
    except Exception as e:
        _rutina_fallo("habito_marcar", e)
        return False


def entreno_hecho(fecha) -> bool:
    """True si ese día hay una sesión de gimnasio hecha: evento de agenda de tipo 'entreno',
    estado 'hecho' y notas que empiezan por '#rot' (las que deja la rutina de gimnasio)."""
    f = _agenda_fecha(fecha)
    dueno = agenda_dueno()
    if not f or not dueno:
        return False
    try:
        r = get_client().table("agenda_eventos").select("id").eq("user_id", dueno).eq("fecha", f) \
            .eq("tipo", "entreno").eq("estado", "hecho").like("notas", "#rot%").limit(1).execute()
        return bool(r.data)
    except Exception as e:
        _rutina_fallo("entreno_hecho", e)
        return False

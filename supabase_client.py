"""
supabase_client.py — Operaciones cloud del Sistema de Vida.
Usado por vida_bot.py cuando corre en Fly.io (IS_CLOUD=True).
"""
import os
import json
import logging
import unicodedata
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
        r = get_client().table("datos_enviados").select("titulo").order("enviado_at", desc=True).limit(n).execute()
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

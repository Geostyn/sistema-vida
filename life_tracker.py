"""
life_tracker.py — Escribe en los archivos Obsidian del Sistema de Vida.
Usado por vida_bot.py para registrar entradas dictadas por el usuario.
"""
import os
import re
from datetime import datetime

VAULT = r"C:\Users\geost\Desktop\Obsidian SC Claude Code"


def _read(path: str) -> str:
    try:
        return open(path, "r", encoding="utf-8").read()
    except FileNotFoundError:
        return ""


def _write(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _month_key() -> str:
    return datetime.now().strftime("%Y-%m")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _day_row_pattern(day_num: int) -> str:
    """Devuelve el patrón para el día en la tabla."""
    return str(day_num).zfill(2)


# ────────────────────────────────────────────────────────────────
# LIBRO 1 — Deporte
# ────────────────────────────────────────────────────────────────

def add_deporte_entry(actividad: str, duracion: str, distancia: str, sensacion: str, notas: str = ""):
    """Registra un entrenamiento en el log mensual."""
    mk   = _month_key()
    path = os.path.join(VAULT, "libro-1-cuerpo-mente-alma", "deporte", f"deporte-{mk}.md")
    day  = datetime.now().day
    text = _read(path)
    if not text:
        return False

    day_pat = rf"(\| {day:02d}[^\|]*\|)[^\n]+"
    new_row = f"\\1 {actividad} | {duracion} | {distancia} | {sensacion} | {notas} |"
    updated = re.sub(day_pat, new_row, text, count=1)
    if updated != text:
        _write(path, updated)
        return True
    return False


# ────────────────────────────────────────────────────────────────
# LIBRO 1 — Alimentación
# ────────────────────────────────────────────────────────────────

def add_alimentacion_entry(desayuno: str, comida: str, cena: str, snacks: str,
                            kcal: str, prot: str, carbs: str, grasas: str,
                            agua: str, energia: str):
    """Registra la alimentación del día con macros."""
    mk   = _month_key()
    path = os.path.join(VAULT, "libro-1-cuerpo-mente-alma", "alimentacion", f"alimentacion-{mk}.md")
    day  = datetime.now().day
    text = _read(path)
    if not text:
        return False

    day_pat = rf"(\| {day:02d}[^\|]*\|)[^\n]+"
    new_row = (f"\\1 {desayuno} | {comida} | {cena} | {snacks} | "
               f"{kcal} | {prot} | {carbs} | {grasas} | {agua} | {energia} |")
    updated = re.sub(day_pat, new_row, text, count=1)
    if updated != text:
        _write(path, updated)
        return True
    return False


# ────────────────────────────────────────────────────────────────
# LIBRO 1 — Léxico
# ────────────────────────────────────────────────────────────────

def add_lexico_entry(palabra: str, definicion: str, ejemplo: str):
    """Añade una palabra nueva al léxico."""
    path = os.path.join(VAULT, "libro-1-cuerpo-mente-alma", "conocimiento", "lexico.md")
    text = _read(path)
    if not text:
        return False

    # Encontrar el último número de fila
    nums = re.findall(r"^\| (\d+) \|", text, re.MULTILINE)
    next_num = (int(nums[-1]) + 1) if nums else 1

    new_row = f"| {next_num} | {palabra} | {definicion} | {ejemplo} | {_today()} |"
    # Insertar antes de "## 📊 Estadísticas" o al final de la tabla
    marker = "## 📊 Estadísticas"
    if marker in text:
        text = text.replace(marker, f"{new_row}\n\n{marker}")
    else:
        text = text.rstrip() + f"\n{new_row}\n"

    # Actualizar contador
    text = re.sub(r"\*\*Total palabras:\*\* \d+", f"**Total palabras:** {next_num}", text)
    _write(path, text)
    return True


# ────────────────────────────────────────────────────────────────
# LIBRO 1 — Refranes
# ────────────────────────────────────────────────────────────────

def add_refran_entry(refran: str, significado: str, contexto: str):
    """Añade un refrán nuevo."""
    path = os.path.join(VAULT, "libro-1-cuerpo-mente-alma", "conocimiento", "refranes.md")
    text = _read(path)
    if not text:
        return False

    nums = re.findall(r"^\| (\d+) \|", text, re.MULTILINE)
    next_num = (int(nums[-1]) + 1) if nums else 1

    new_row = f"| {next_num} | {refran} | {significado} | {contexto} | {_today()} |"
    marker = "## 📊 Estadísticas"
    if marker in text:
        text = text.replace(marker, f"{new_row}\n\n{marker}")
    else:
        text = text.rstrip() + f"\n{new_row}\n"

    text = re.sub(r"\*\*Total refranes:\*\* \d+", f"**Total refranes:** {next_num}", text)
    _write(path, text)
    return True


# ────────────────────────────────────────────────────────────────
# LIBRO 2 — Gastos
# ────────────────────────────────────────────────────────────────

def add_gasto_entry(categoria: str, concepto: str, importe: float):
    """Registra un gasto en el log mensual."""
    mk   = _month_key()
    path = os.path.join(VAULT, "libro-2-finanzas-negocios", "finanzas", f"gastos-{mk}.md")
    text = _read(path)
    if not text:
        return False

    fecha = datetime.now().strftime("%d/%m")
    new_row = f"| {fecha} | {categoria} | {concepto} | €{importe:.2f} |"

    # Insertar en la semana correcta (primera fila vacía de la semana actual)
    week_sections = ["### Semana 1", "### Semana 2", "### Semana 3", "### Semana 4", "### Semana 5"]
    day_of_month = datetime.now().day
    week_idx = min((day_of_month - 1) // 7, 4)
    week_header = week_sections[week_idx]

    if week_header in text:
        # Insertar antes del subtotal de la semana
        subtotal_pat = rf"(\| \*\*Subtotal semana {week_idx + 1}\*\*)"
        if re.search(subtotal_pat, text):
            text = re.sub(subtotal_pat, f"{new_row}\n\\1", text, count=1)
        else:
            text = text.replace(week_header, week_header)
    else:
        text = text.rstrip() + f"\n{new_row}\n"

    _write(path, text)
    return True


# ────────────────────────────────────────────────────────────────
# LIBRO 2 — Ideas de negocio
# ────────────────────────────────────────────────────────────────

def add_idea_rapida(idea: str, inversion: str = "—", tiempo: str = "—",
                    potencial: str = "—"):
    """Añade una idea al backlog."""
    path = os.path.join(VAULT, "libro-2-finanzas-negocios", "negocios", "ideas-rapidas.md")
    text = _read(path)
    if not text:
        return False

    nums = re.findall(r"^\| (\d+) \|", text, re.MULTILINE)
    next_num = (int(nums[-1]) + 1) if nums else 1

    new_row = f"| {next_num} | {idea} | {inversion} | {tiempo} | {potencial} | 💡 Nueva | {_today()} |"
    marker = "## 🏷️ Estados"
    if marker in text:
        text = text.replace(marker, f"{new_row}\n\n{marker}")
    else:
        text = text.rstrip() + f"\n{new_row}\n"

    _write(path, text)
    return True


# ────────────────────────────────────────────────────────────────
# LIBRO 2 — Trading reflexiones
# ────────────────────────────────────────────────────────────────

def add_trading_reflexion(observacion: str, tipo: str = "💡", accion: str = "—"):
    """Añade una reflexión de trading."""
    path = os.path.join(VAULT, "libro-2-finanzas-negocios", "trading", "trading-reflexiones.md")
    text = _read(path)
    if not text:
        return False

    fecha = _today()
    new_row = f"| {fecha} | {observacion} | {tipo} | {accion} |"

    # Insertar al final de la tabla del mes actual o al final
    marker = "## 🧠 Patrones Observados"
    if marker in text:
        text = text.replace(marker, f"{new_row}\n\n{marker}")
    else:
        text = text.rstrip() + f"\n{new_row}\n"

    _write(path, text)
    return True


# ────────────────────────────────────────────────────────────────
# LIBRO 3 — Diario / Planificación
# ────────────────────────────────────────────────────────────────

def add_diario_entry(lo_importante: str, gratitud: str, mejora: str, habitos_ok: str):
    """Registra la entrada del día en el diario mensual."""
    mk   = _month_key()
    path = os.path.join(VAULT, "libro-3-planificacion", "meses", f"planificacion-{mk}.md")
    day  = datetime.now().day
    text = _read(path)
    if not text:
        return False

    day_pat = rf"(\| {day:02d}[^\|]*\|)[^\n]+"
    new_row = f"\\1 {lo_importante} | {gratitud} | {mejora} | {habitos_ok} |"
    updated = re.sub(day_pat, new_row, text, count=1)
    if updated != text:
        _write(path, updated)
        return True
    return False


# ────────────────────────────────────────────────────────────────
# Daily Note
# ────────────────────────────────────────────────────────────────

def create_daily_note(content: str):
    """Crea la nota diaria si no existe."""
    today = _today()
    path  = os.path.join(VAULT, "daily-notes", f"{today}.md")
    if not os.path.exists(path):
        _write(path, content)
        return True
    else:
        # Añadir contenido al final si ya existe
        existing = _read(path)
        _write(path, existing + "\n---\n" + content)
        return True


# ────────────────────────────────────────────────────────────────
# ESTADO-VIDA.md (actualización rápida desde el bot)
# ────────────────────────────────────────────────────────────────

def update_vida_state_quick(updates: dict):
    """Actualiza campos específicos en ESTADO-VIDA.md de forma rápida."""
    path = os.path.join(VAULT, "ESTADO-VIDA.md")
    text = _read(path)
    if not text:
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"\*\*Última actualización:\*\*.*", f"**Última actualización:** {now_str}", text)

    for key, value in updates.items():
        text = re.sub(rf"\| {re.escape(key)} \|.*", f"| {key} | {value} |", text)

    _write(path, text)

"""
setup_notion.py — Crea automáticamente todas las databases del Sistema de Vida en Notion.

Uso:
  python setup_notion.py --token TU_INTEGRATION_TOKEN --page-id TU_PAGE_ID

Cómo obtener los parámetros:
  1. Ve a notion.so/my-integrations → Crear nueva integración → copia el token
  2. Crea una página en Notion llamada "Sistema de Vida"
  3. Abre la página → copia los últimos 32 caracteres de la URL (sin guiones)
     Ej: notion.so/Tu-Nombre/Sistema-de-Vida-[ESTE-ES-EL-ID]
  4. Conecta la integración: en la página → ... → Conexiones → tu integración
  5. Ejecuta: python setup_notion.py --token <token> --page-id <page_id>
"""

import sys
import json
import argparse
import requests

NOTION_API = "https://api.notion.com/v1"
HEADERS_BASE = {
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def headers(token: str) -> dict:
    return {**HEADERS_BASE, "Authorization": f"Bearer {token}"}


def create_database(token: str, parent_page_id: str, title: str, properties: dict, icon: str = "📊") -> dict:
    """Crea una database en Notion bajo la página padre."""
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "icon": {"type": "emoji", "emoji": icon},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": properties
    }
    resp = requests.post(f"{NOTION_API}/databases", headers=headers(token), json=payload)
    if resp.ok:
        print(f"  ✅ Creada: {title}")
        return resp.json()
    else:
        print(f"  ❌ Error creando {title}: {resp.text[:200]}")
        return {}


def create_page(token: str, parent_page_id: str, title: str, icon: str = "📄") -> dict:
    """Crea una página en Notion."""
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "icon": {"type": "emoji", "emoji": icon},
        "properties": {
            "title": {"title": [{"text": {"content": title}}]}
        }
    }
    resp = requests.post(f"{NOTION_API}/pages", headers=headers(token), json=payload)
    if resp.ok:
        print(f"  ✅ Creada página: {title}")
        return resp.json()
    else:
        print(f"  ❌ Error creando {title}: {resp.text[:200]}")
        return {}


def setup_libro1(token: str, parent_id: str):
    """Crea las databases del Libro 1 — Cuerpo, Mente y Alma."""
    print("\n📗 Creando Libro 1 — Cuerpo, Mente y Alma...")

    # Página del libro
    libro1 = create_page(token, parent_id, "📗 Libro 1 — Cuerpo, Mente y Alma", "📗")
    if not libro1:
        return
    lib1_id = libro1["id"]

    # 🏃 Deporte Log
    create_database(token, lib1_id, "🏃 Deporte Log", {
        "Fecha": {"date": {}},
        "Actividad": {"title": {}},
        "Duración": {"rich_text": {}},
        "Distancia / Series": {"rich_text": {}},
        "Sensación": {"select": {"options": [
            {"name": "🔥 Excelente", "color": "red"},
            {"name": "😊 Bien", "color": "green"},
            {"name": "😐 Normal", "color": "yellow"},
            {"name": "😓 Difícil", "color": "orange"},
            {"name": "😴 Mal", "color": "gray"},
        ]}},
        "Notas": {"rich_text": {}},
    }, "🏃")

    # 🍽️ Alimentación
    create_database(token, lib1_id, "🍽️ Alimentación & Macros", {
        "Fecha": {"date": {}},
        "Desayuno": {"title": {}},
        "Comida": {"rich_text": {}},
        "Cena": {"rich_text": {}},
        "Snacks": {"rich_text": {}},
        "Kcal": {"number": {"format": "number"}},
        "Proteína (g)": {"number": {"format": "number"}},
        "Carbos (g)": {"number": {"format": "number"}},
        "Grasas (g)": {"number": {"format": "number"}},
        "Agua (L)": {"number": {"format": "number"}},
        "Energía": {"select": {"options": [
            {"name": "🔥 Alta", "color": "red"},
            {"name": "😊 Normal", "color": "green"},
            {"name": "😴 Baja", "color": "gray"},
        ]}},
    }, "🍽️")

    # ✅ Hábitos Diarios
    create_database(token, lib1_id, "✅ Hábitos Diarios", {
        "Fecha": {"date": {}},
        "Día": {"title": {}},
        "Ducha fría": {"checkbox": {}},
        "Té de clavo": {"checkbox": {}},
        "Oración": {"checkbox": {}},
        "Silencio / Reflexión": {"checkbox": {}},
        "Lectura espiritual": {"checkbox": {}},
        "Hábito extra": {"rich_text": {}},
        "Notas": {"rich_text": {}},
    }, "✅")

    # 📖 Léxico
    create_database(token, lib1_id, "📖 Léxico — Vocabulario", {
        "Palabra": {"title": {}},
        "Definición": {"rich_text": {}},
        "Ejemplo de uso": {"rich_text": {}},
        "Fecha aprendida": {"date": {}},
        "Categoría": {"select": {"options": [
            {"name": "General", "color": "blue"},
            {"name": "Técnico", "color": "orange"},
            {"name": "Coloquial", "color": "green"},
        ]}},
    }, "📖")

    # 💬 Refranes
    create_database(token, lib1_id, "💬 Refranes y Dichos", {
        "Refrán": {"title": {}},
        "Significado": {"rich_text": {}},
        "Contexto / Origen": {"rich_text": {}},
        "Fecha": {"date": {}},
        "Categoría": {"select": {"options": [
            {"name": "💪 Esfuerzo", "color": "red"},
            {"name": "🧠 Sabiduría", "color": "blue"},
            {"name": "💰 Dinero", "color": "yellow"},
            {"name": "🤝 Relaciones", "color": "green"},
            {"name": "🌱 Vida", "color": "gray"},
        ]}},
    }, "💬")


def setup_libro2(token: str, parent_id: str):
    """Crea las databases del Libro 2 — Finanzas y Negocios."""
    print("\n📒 Creando Libro 2 — Finanzas y Negocios...")

    libro2 = create_page(token, parent_id, "📒 Libro 2 — Finanzas y Negocios", "📒")
    if not libro2:
        return
    lib2_id = libro2["id"]

    # 💶 Gastos
    create_database(token, lib2_id, "💶 Registro de Gastos", {
        "Concepto": {"title": {}},
        "Fecha": {"date": {}},
        "Categoría": {"select": {"options": [
            {"name": "🍔 Comida / Supermercado", "color": "orange"},
            {"name": "🚌 Transporte", "color": "blue"},
            {"name": "🎮 Ocio / Entretenimiento", "color": "purple"},
            {"name": "👕 Ropa", "color": "pink"},
            {"name": "💊 Salud", "color": "red"},
            {"name": "📚 Formación", "color": "yellow"},
            {"name": "💰 Ahorro / Inversión", "color": "green"},
            {"name": "🏠 Hogar / Varios", "color": "brown"},
            {"name": "➕ Extra (libre)", "color": "gray"},
        ]}},
        "Importe (€)": {"number": {"format": "euro"}},
        "Notas": {"rich_text": {}},
    }, "💶")

    # 📊 Presupuesto
    create_database(token, lib2_id, "📊 Presupuesto Mensual", {
        "Categoría": {"title": {}},
        "Límite mensual (€)": {"number": {"format": "euro"}},
        "Gastado actual (€)": {"number": {"format": "euro"}},
        "Mes": {"rich_text": {}},
        "Estado": {"select": {"options": [
            {"name": "✅ Bien", "color": "green"},
            {"name": "⚠️ Atención", "color": "yellow"},
            {"name": "❌ Superado", "color": "red"},
        ]}},
    }, "📊")

    # 💼 Ideas de Negocio
    create_database(token, lib2_id, "💼 Ideas de Negocio", {
        "Idea": {"title": {}},
        "Descripción": {"rich_text": {}},
        "Inversión inicial": {"rich_text": {}},
        "Tiempo para monetizar": {"rich_text": {}},
        "Potencial": {"select": {"options": [
            {"name": "⭐⭐⭐⭐⭐ Muy alto", "color": "red"},
            {"name": "⭐⭐⭐⭐ Alto", "color": "orange"},
            {"name": "⭐⭐⭐ Medio", "color": "yellow"},
            {"name": "⭐⭐ Bajo", "color": "gray"},
        ]}},
        "Estado": {"select": {"options": [
            {"name": "💡 Nueva", "color": "yellow"},
            {"name": "🔍 Analizando", "color": "blue"},
            {"name": "🚀 Activa", "color": "green"},
            {"name": "🗃️ Archivada", "color": "gray"},
        ]}},
        "Fecha añadida": {"date": {}},
        "Tareas": {"rich_text": {}},
    }, "💼")

    # 📈 Trading Reflexiones
    create_database(token, lib2_id, "📈 Trading — Reflexiones", {
        "Observación": {"title": {}},
        "Fecha": {"date": {}},
        "Tipo": {"select": {"options": [
            {"name": "💡 Lección", "color": "yellow"},
            {"name": "📊 Patrón", "color": "blue"},
            {"name": "⚠️ Error", "color": "red"},
            {"name": "✅ Acierto", "color": "green"},
        ]}},
        "Acción a tomar": {"rich_text": {}},
    }, "📈")


def setup_libro3(token: str, parent_id: str):
    """Crea las databases del Libro 3 — Planificación."""
    print("\n📘 Creando Libro 3 — Planificación y Control Personal...")

    libro3 = create_page(token, parent_id, "📘 Libro 3 — Planificación y Control", "📘")
    if not libro3:
        return
    lib3_id = libro3["id"]

    # 🎯 Metas Anuales
    create_database(token, lib3_id, "🎯 Metas Anuales", {
        "Meta": {"title": {}},
        "Descripción": {"rich_text": {}},
        "Categoría": {"select": {"options": [
            {"name": "💰 Financiera", "color": "yellow"},
            {"name": "💪 Salud", "color": "green"},
            {"name": "📈 Trading", "color": "blue"},
            {"name": "💼 Negocio", "color": "purple"},
            {"name": "🧠 Personal", "color": "orange"},
        ]}},
        "Fecha límite": {"date": {}},
        "Progreso (%)": {"number": {"format": "percent"}},
        "Estado": {"select": {"options": [
            {"name": "⏳ Pendiente", "color": "gray"},
            {"name": "🚀 En progreso", "color": "blue"},
            {"name": "✅ Completada", "color": "green"},
            {"name": "❌ Descartada", "color": "red"},
        ]}},
        "Milestones": {"rich_text": {}},
    }, "🎯")

    # 📅 Metas Mensuales
    create_database(token, lib3_id, "📅 Metas Mensuales", {
        "Meta": {"title": {}},
        "Mes": {"rich_text": {}},
        "Cómo medirla": {"rich_text": {}},
        "Estado": {"select": {"options": [
            {"name": "⏳ Pendiente", "color": "gray"},
            {"name": "🚀 En progreso", "color": "blue"},
            {"name": "✅ Completada", "color": "green"},
            {"name": "❌ No lograda", "color": "red"},
        ]}},
        "Revisión": {"rich_text": {}},
    }, "📅")

    # 📖 Diario de Vida
    create_database(token, lib3_id, "📖 Diario de Vida", {
        "Fecha": {"date": {}},
        "Día": {"title": {}},
        "Lo más importante": {"rich_text": {}},
        "Gratitud": {"rich_text": {}},
        "Algo a mejorar": {"rich_text": {}},
        "Hábitos cumplidos": {"rich_text": {}},
        "Energía general": {"select": {"options": [
            {"name": "🔥 Alta", "color": "red"},
            {"name": "😊 Normal", "color": "green"},
            {"name": "😴 Baja", "color": "gray"},
        ]}},
    }, "📖")


def main():
    parser = argparse.ArgumentParser(description="Setup automático de Notion para el Sistema de Vida")
    parser.add_argument("--token", required=True, help="Notion Integration Token (empieza con 'secret_')")
    parser.add_argument("--page-id", required=True, help="ID de la página 'Sistema de Vida' en Notion")
    args = parser.parse_args()

    token   = args.token.strip()
    page_id = args.page_id.strip().replace("-", "")

    # Verificar acceso
    resp = requests.get(f"{NOTION_API}/pages/{page_id}", headers=headers(token))
    if not resp.ok:
        print(f"❌ No se puede acceder a la página. Verifica el token y el page_id.")
        print(f"   Error: {resp.text[:300]}")
        sys.exit(1)

    page_title = resp.json().get("properties", {}).get("title", {}).get("title", [{}])[0].get("plain_text", "página")
    print(f"\n✅ Conectado a Notion. Página: '{page_title}'")
    print("Creando bases de datos...\n")

    setup_libro1(token, page_id)
    setup_libro2(token, page_id)
    setup_libro3(token, page_id)

    print("\n🎉 ¡Setup completado!")
    print("Abre Notion en tu teléfono para ver las 3 secciones con todas las databases.")
    print("\nPróximo paso: ejecuta vida_bot.py para empezar a dictar tu día desde Telegram.")


if __name__ == "__main__":
    main()

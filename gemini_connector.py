"""
Gemini Vision — analiza páginas de libros con imagen.
Package: pip install google-generativeai>=0.8.0
Secret needed: GEMINI_API_KEY

Usage:
    result = analyze_book_page(api_key, image_bytes, "Meditaciones - Marco Aurelio")
    # result = {
    #   "explicacion": "Este fragmento trata sobre...",
    #   "palabras": [
    #     {"palabra": "Hegemonikon", "definicion": "Parte rectora del alma...", "uso_en_libro": "«el hegemonikon debe...»"},
    #   ]
    # }
"""
import json
import re


_PROMPT = """Eres un tutor experto en filosofía clásica y estoicismo. El usuario está leyendo '{libro}'.

Analiza el texto de esta página de libro y responde en JSON con esta estructura EXACTA (sin texto extra):
{{
  "explicacion": "Explica en español claro y cercano los conceptos principales de este fragmento. 2-3 párrafos. Usa ejemplos modernos si ayudan a entender.",
  "palabras": [
    {{
      "palabra": "la palabra exacta del texto",
      "definicion": "significado claro en español",
      "uso_en_libro": "la frase o fragmento exacto del texto donde aparece esta palabra"
    }}
  ]
}}

Incluye en "palabras" solo términos que podrían ser desconocidos: palabras filosóficas, latinas/griegas, arcaicas o especializadas. Si no hay palabras difíciles, devuelve "palabras": [].
Responde SOLO el JSON, sin markdown, sin ```json, sin explicaciones adicionales."""


def analyze_book_page(api_key: str, image_bytes: bytes, libro_nombre: str = "libro") -> dict:
    """
    Send a book page image to Gemini and get explanation + vocabulary.

    Returns dict: {"explicacion": str, "palabras": [{"palabra", "definicion", "uso_en_libro"}]}
    Returns {"error": str} on failure.
    """
    try:
        import google.generativeai as genai
        from google.generativeai.types import HarmCategory, HarmBlockThreshold

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash-latest",
            safety_settings={
                HarmCategory.HARM_CATEGORY_HARASSMENT:        HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH:       HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            },
        )

        import PIL.Image
        import io
        img = PIL.Image.open(io.BytesIO(image_bytes))

        prompt = _PROMPT.format(libro=libro_nombre)
        response = model.generate_content(
            [prompt, img],
            generation_config={"temperature": 0.3, "max_output_tokens": 2048},
            request_options={"timeout": 40},
        )

        raw = response.text.strip()
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()

        result = json.loads(raw)
        if "explicacion" not in result:
            return {"error": "Respuesta incompleta de Gemini."}
        if "palabras" not in result:
            result["palabras"] = []
        return result

    except json.JSONDecodeError:
        return {"error": "Gemini no devolvió JSON válido. Intenta de nuevo.", "raw": raw if "raw" in dir() else ""}
    except Exception as e:
        return {"error": str(e)}

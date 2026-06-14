"""
Espera a que el dashboard esté listo y luego abre el navegador.
Se usa desde INICIAR.bat.
"""
import time
import webbrowser
import urllib.request
import urllib.error
import sys

URL = "http://127.0.0.1:8501"
HEALTH = "http://127.0.0.1:8501/_stcore/health"
MAX_ESPERA = 60  # segundos máximo de espera

print("  Esperando que el dashboard cargue", end="", flush=True)

for i in range(MAX_ESPERA):
    try:
        urllib.request.urlopen(HEALTH, timeout=2)
        # Si llega aquí, streamlit ya responde
        print(f"\n  Dashboard listo en {i+1} segundos.")
        time.sleep(1)
        # Abrir en Edge
        webbrowser.get("windows-default").open(URL)
        print(f"  Navegador abierto: {URL}")
        sys.exit(0)
    except Exception:
        print(".", end="", flush=True)
        time.sleep(1)

print("\n  ERROR: El dashboard tardó demasiado en arrancar.")
print(f"  Abre este link manualmente en Edge: {URL}")

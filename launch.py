"""
Lanzador rapido del Sistema de Trading XAUUSD.
Cada paso avanza en cuanto el componente esta listo, sin tiempos fijos.
"""
import os, sys, subprocess, time, urllib.request

BASE   = os.path.dirname(os.path.abspath(__file__))
PYTHON = r"C:\Users\geost\AppData\Local\Python\pythoncore-3.14-64\python.exe"
MT5    = r"C:\Program Files\MetaTrader 5\terminal64.exe"
OPERA  = r"C:\Users\geost\AppData\Local\Programs\Opera GX\opera.exe"
URL    = "http://127.0.0.1:8501"

os.environ["PYTHONIOENCODING"]  = "utf-8"
os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"


def esperar(condicion, label, max_seg=40):
    """Espera hasta que condicion() sea True. Muestra puntos en pantalla."""
    print(f"       Esperando {label}", end="", flush=True)
    for i in range(max_seg * 2):
        if condicion():
            print(f" listo ({i//2 + 1}s)")
            return True
        print(".", end="", flush=True)
        time.sleep(0.5)
    print(f" (continuo despues de {max_seg}s)")
    return False


def mt5_corriendo():
    r = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq terminal64.exe"],
        capture_output=True, text=True
    )
    return "terminal64.exe" in r.stdout


def market_state_existe():
    return os.path.exists(os.path.join(BASE, "logs", "market_state.json"))


def dashboard_listo():
    try:
        urllib.request.urlopen(f"{URL}/_stcore/health", timeout=1)
        return True
    except Exception:
        return False


def nueva_ventana(titulo, comando):
    subprocess.Popen(
        ["cmd", "/k", comando],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        cwd=BASE
    )


# ── INICIO ────────────────────────────────────────────────────

os.system("cls")
print()
print("  ============================================")
print("   SISTEMA DE TRADING XAUUSD")
print("  ============================================")
print()

inicio = time.time()

# 1 - MetaTrader 5
print("  [1/3] MetaTrader 5...")
if not mt5_corriendo():
    os.startfile(MT5)
    esperar(mt5_corriendo, "que MT5 abra", max_seg=30)
else:
    print("       Ya estaba abierto")
# Dar 3 segundos extra para que MT5 se conecte al servidor
time.sleep(3)

# 2 - Motor de analisis
print("  [2/3] Motor de analisis...")
# Borrar market_state viejo para detectar cuando el nuevo ciclo termine
mj = os.path.join(BASE, "logs", "market_state.json")
if os.path.exists(mj):
    os.remove(mj)

nueva_ventana(
    "MOTOR TRADING",
    f"set PYTHONIOENCODING=utf-8 & cd /d {BASE} & {PYTHON} main.py"
)
esperar(market_state_existe, "primer ciclo de analisis", max_seg=35)

# 3 - Dashboard
print("  [3/3] Dashboard...")
nueva_ventana(
    "DASHBOARD",
    f"set STREAMLIT_BROWSER_GATHER_USAGE_STATS=false & cd /d {BASE} & {PYTHON} -m streamlit run dashboard/app.py"
)
esperar(dashboard_listo, "que dashboard cargue", max_seg=35)

# Abrir en Opera GX con 127.0.0.1 (Opera bloquea "localhost" pero no 127.0.0.1)
time.sleep(0.5)
subprocess.Popen([OPERA, URL])

# ── LISTO ─────────────────────────────────────────────────────

total = int(time.time() - inicio)
os.system("cls")
print()
print("  ============================================")
print(f"   SISTEMA ACTIVO  ({total}s)")
print()
print("   Telegram: mensaje SISTEMA ACTIVO enviado")
print(f"   Dashboard: {URL}")
print()
print("   Para apagar: doble click en DETENER.bat")
print("  ============================================")
print()
input("  Presiona Enter para cerrar esta ventana...")

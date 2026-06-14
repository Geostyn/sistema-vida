"""Test completo del sistema de lanzamiento."""
import subprocess, sys, os, time, urllib.request

BASE   = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
os.environ["PYTHONIOENCODING"]  = "utf-8"
os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

print("=" * 50)
print("  PRUEBA COMPLETA DEL SISTEMA")
print("=" * 50)

# TEST 1: Motor
print("\n[1/2] Probando motor de analisis...")
r = subprocess.run(
    [PYTHON, os.path.join(BASE, "main.py"), "--test"],
    cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    timeout=30, env=os.environ.copy()
)
output = r.stdout.decode("utf-8", errors="replace")
errors = r.stderr.decode("utf-8", errors="replace")

if "completado" in output:
    # Extraer lineas clave
    for line in output.splitlines():
        if any(x in line for x in ["MT5 conectado", "Telegram", "SENAL", "completado"]):
            print("  " + line.strip())
    print("  Motor: OK")
else:
    print("  Motor: ERROR")
    print(output[-800:])
    print(errors[-400:])
    sys.exit(1)

# TEST 2: Dashboard
print("\n[2/2] Probando dashboard...")
dash = subprocess.Popen(
    [PYTHON, "-m", "streamlit", "run",
     os.path.join(BASE, "dashboard", "app.py")],
    cwd=BASE, env=os.environ.copy(),
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)

ok = False
print("  Esperando", end="", flush=True)
for i in range(30):
    print(".", end="", flush=True)
    time.sleep(1)
    try:
        urllib.request.urlopen("http://127.0.0.1:8501/_stcore/health", timeout=2)
        print(f" OK ({i+1}s)")
        ok = True
        break
    except Exception:
        pass

dash.terminate()

if not ok:
    print("\n  Dashboard: ERROR - no respondio")
    sys.exit(1)

print("  Dashboard: OK")
print()
print("=" * 50)
print("  TODO FUNCIONA - Sistema listo para usar")
print("  Ejecuta INICIAR.bat para lanzar todo")
print("=" * 50)

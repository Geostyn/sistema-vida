"""Prueba si se pueden abrir ventanas nuevas correctamente."""
import subprocess, sys, os, time

BASE   = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable

print("Probando apertura de ventanas...")
print(f"Python: {PYTHON}")
print()

# Test 1: CREATE_NEW_CONSOLE
print("[1] CREATE_NEW_CONSOLE...")
try:
    p = subprocess.Popen(
        [PYTHON, "-c", "import time; print('ventana test OK'); time.sleep(5)"],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        cwd=BASE
    )
    time.sleep(2)
    alive = p.poll() is None
    print(f"    PID: {p.pid} | Viva: {alive}")
    p.terminate()
except Exception as e:
    print(f"    ERROR: {e}")

# Test 2: cmd /c start
print("[2] cmd /c start...")
try:
    p2 = subprocess.Popen(
        ["cmd", "/c", f'start "MOTOR-TEST" {PYTHON} -c "import time; print(chr(79)+chr(75)); time.sleep(5)"'],
        cwd=BASE,
        shell=True
    )
    time.sleep(2)
    print(f"    PID: {p2.pid} | OK")
except Exception as e:
    print(f"    ERROR: {e}")

# Test 3: start directo con shell=True
print("[3] shell=True start...")
try:
    cmd = f'start "DASHBOARD-TEST" cmd /k "echo TEST OK && timeout 5"'
    p3 = subprocess.Popen(cmd, shell=True, cwd=BASE)
    time.sleep(2)
    print(f"    PID: {p3.pid} | OK")
except Exception as e:
    print(f"    ERROR: {e}")

print()
print("Verifica si aparecieron ventanas nuevas en tu pantalla.")

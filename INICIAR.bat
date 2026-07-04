@echo off
cd /d "C:\Users\geost\Desktop\trading-system"
set PYTHONIOENCODING=utf-8
set STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
:: Reactivar el watchdog (borra la pausa que crea DETENER.bat) y asegurar
:: que el bucle watchdog corre (idempotente: si ya hay uno, el nuevo sale)
if exist logs\watchdog.pause del logs\watchdog.pause
call "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\trading-watchdog.bat"
C:\Users\geost\AppData\Local\Python\pythoncore-3.14-64\python.exe launch.py
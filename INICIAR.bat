@echo off
cd /d "C:\Users\geost\Desktop\trading-system"
set PYTHONIOENCODING=utf-8
set STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
:: Reactivar el watchdog (borra la pausa que crea DETENER.bat)
if exist logs\watchdog.pause del logs\watchdog.pause
C:\Users\geost\AppData\Local\Python\pythoncore-3.14-64\python.exe launch.py
@echo off
chcp 65001 >nul
cd /d "C:\Users\geost\Desktop\trading-system"
set PYTHONIOENCODING=utf-8
echo.
echo   Pensando en el oro... (radiografia en vivo de XAUUSD)
echo.
C:\Users\geost\AppData\Local\Python\pythoncore-3.14-64\python.exe radiografia.py
echo.
pause

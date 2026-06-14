@echo off
chcp 65001 >nul
cd /d "C:\Users\geost\Desktop\trading-system"
set PYTHONIOENCODING=utf-8

echo.
echo  Apagando el sistema de trading...
echo.

:: Avisar a Telegram antes de matar los procesos
echo  Enviando mensaje de sistema inactivo a Telegram...
python enviar_inactivo.py
echo.

:: Cerrar ventanas del sistema
taskkill /fi "WINDOWTITLE eq MOTOR DE TRADING - NO CERRAR" /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq DASHBOARD TRADING - NO CERRAR" /f >nul 2>&1
taskkill /f /fi "IMAGENAME eq python.exe" >nul 2>&1

echo  Sistema apagado correctamente.
echo  Revisa tu Telegram: debio llegar mensaje de SISTEMA INACTIVO.
echo.
pause

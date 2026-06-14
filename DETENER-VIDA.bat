@echo off
title Detener Vida Bot
echo Buscando proceso vida_bot.py...
taskkill /F /FI "WINDOWTITLE eq Sistema de Vida*" /T >nul 2>&1
for /f "tokens=2" %%a in ('tasklist /fi "imagename eq python.exe" /fo list ^| findstr /i "PID"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /i "vida_bot" >nul
    if not errorlevel 1 (
        taskkill /F /PID %%a >nul 2>&1
        echo Vida Bot detenido ^(PID %%a^).
    )
)
echo.
echo Proceso completado.
pause

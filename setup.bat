@echo off
chcp 65001 >nul
echo.
echo ============================================================
echo   SISTEMA DE TRADING XAUUSD - Instalacion de dependencias
echo ============================================================
echo.

echo [1/4] Instalando dependencias Python...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Fallo al instalar dependencias
    echo Asegurate de tener Python instalado: https://www.python.org
    pause
    exit /b 1
)

echo.
echo [2/4] Verificando instalaciones...
python -c "import MetaTrader5; print('  [OK] MetaTrader5')" 2>nul || echo "  [ERROR] MetaTrader5 - instala MT5 primero"
python -c "import streamlit; print('  [OK] Streamlit')"
python -c "import pandas_ta; print('  [OK] pandas-ta')"
python -c "import plotly; print('  [OK] plotly')"
python -c "import finnhub; print('  [OK] finnhub-python')"
python -c "import schedule; print('  [OK] schedule')"

echo.
echo [3/4] Creando carpeta de logs...
if not exist "logs" mkdir logs

echo.
echo [4/4] Verificando config.yaml...
if not exist "config.yaml" (
    echo   [ERROR] config.yaml no encontrado
) else (
    echo   [OK] config.yaml encontrado - recuerda editarlo con tus datos
)

echo.
echo ============================================================
echo   INSTALACION COMPLETADA
echo.
echo   Proximos pasos OBLIGATORIOS:
echo   1. Edita config.yaml con:
echo      - Numero de cuenta MT5 (o deja 0 para sesion activa)
echo      - API key de Finnhub (gratis en finnhub.io)
echo      - Token y Chat ID de Telegram
echo.
echo   Para ejecutar el sistema:
echo   - Terminal 1: python main.py
echo   - Terminal 2: streamlit run dashboard/app.py
echo.
echo   Para backtest: python main.py --backtest
echo ============================================================
echo.
pause

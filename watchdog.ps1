# Watchdog del bot de trading — relanza main.py si se cae.
# Motivo: el bot murio 2 veces en 3 dias (apagon del PC 03-07, ventana
# cerrada 04-07) y cada caida son horas sin senales ni gestion de trades.
#
# MODO DE EJECUCION (2026-07-05): bucle persistente lanzado al iniciar sesion
# por el acceso de la carpeta de Inicio:
#   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\trading-watchdog.bat
# Corre minimizado con titulo TRADING-WATCHDOG (visible en la barra de tareas;
# cerrarlo a proposito = apagar el watchdog hasta el proximo inicio de sesion).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File watchdog.ps1 -Loop
#   (sin -Loop hace UNA comprobacion y sale — util para probar a mano)
#
# HISTORIA: la v1 usaba una tarea programada (TradingBotWatchdog) pero algo en
# este Windows la DESHABILITABA tras cada disparo (sin deteccion de Defender;
# probado 3x el 2026-07-05, incluso registrada nativa con Register-ScheduledTask
# — disparaba OK, resultado 0, y ~25 s despues aparecia Disabled). Se abandono
# Task Scheduler por el bucle persistente. NO volver a schtasks sin resolver eso.
#
# PAUSA (apagado manual intencionado): si existe logs\watchdog.pause NO
# relanza. DETENER.bat crea el flag; INICIAR.bat lo borra.

param(
    [switch]$Loop,
    [int]$IntervalSec = 600
)

$base = 'C:\Users\geost\Desktop\trading-system'
$py   = 'C:\Users\geost\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$log  = Join-Path $base 'logs\watchdog.log'

function Invoke-WatchdogCheck {
    if (Test-Path (Join-Path $base 'logs\watchdog.pause')) { return }

    # El bot vivo = python.exe DE PRODUCCION cuyo CommandLine contiene main.py
    # (mismo criterio que el reinicio quirurgico de PLAN-FABLE.md)
    $running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match '\bmain\.py' -and $_.ExecutablePath -eq $py }
    if ($running) { return }

    Add-Content $log ("{0}  main.py caido - relanzando via watchdog" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
    Start-Process cmd -WorkingDirectory $base -WindowStyle Minimized -ArgumentList '/k', "title TRADING-BOT & set PYTHONIOENCODING=utf-8 & `"$py`" main.py"
}

if ($Loop) {
    # Idempotente: si ya hay otro watchdog en bucle, salir sin duplicar
    # (permite llamarlo desde INICIAR.bat y el arranque sin riesgo de carrera)
    $others = Get-Process powershell -ErrorAction SilentlyContinue |
        Where-Object { $_.Id -ne $PID -and $_.MainWindowTitle -eq 'TRADING-WATCHDOG' }
    if ($others) { exit 0 }
    $host.UI.RawUI.WindowTitle = 'TRADING-WATCHDOG'
    Add-Content $log ("{0}  watchdog en bucle iniciado (cada {1}s)" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $IntervalSec)
    while ($true) {
        try { Invoke-WatchdogCheck } catch {
            Add-Content $log ("{0}  error: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $_.Exception.Message)
        }
        Start-Sleep -Seconds $IntervalSec
    }
} else {
    Invoke-WatchdogCheck
}

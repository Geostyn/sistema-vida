# Watchdog del bot de trading — relanza main.py si se cae.
# Motivo: el bot murio 2 veces en 3 dias (apagon del PC 03-07, ventana
# cerrada 04-07) y cada caida son horas sin senales ni gestion de trades.
#
# Registrado en el Programador de tareas de Windows como "TradingBotWatchdog"
# (cada 10 min, usuario logueado). Ver/borrar:
#   schtasks /Query /TN TradingBotWatchdog
#   schtasks /Delete /TN TradingBotWatchdog /F
#
# OJO (2026-07-05): NO recrear con `schtasks /Create /TR "...\""` — el escapado
# de comillas corrompe la ruta (-File " C:\...\watchdog.ps1\) y la tarea queda
# rota/deshabilitada. Recrear SIEMPRE con PowerShell nativo:
#   $a = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\geost\Desktop\trading-system\watchdog.ps1"'
#   $t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Days 3650)
#   $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
#   Register-ScheduledTask -TaskName TradingBotWatchdog -Action $a -Trigger $t -Settings $s
#   Enable-ScheduledTask -TaskName TradingBotWatchdog   # a veces registra Disabled
#
# PAUSA (apagado manual intencionado): si existe logs\watchdog.pause NO
# relanza. DETENER.bat crea el flag; INICIAR.bat lo borra.

$base = 'C:\Users\geost\Desktop\trading-system'
$py   = 'C:\Users\geost\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$log  = Join-Path $base 'logs\watchdog.log'

if (Test-Path (Join-Path $base 'logs\watchdog.pause')) { exit 0 }

# El bot vivo = python.exe DE PRODUCCION cuyo CommandLine contiene main.py
# (mismo criterio que el reinicio quirurgico de PLAN-FABLE.md)
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match '\bmain\.py' -and $_.ExecutablePath -eq $py }
if ($running) { exit 0 }

Add-Content $log ("{0}  main.py caido - relanzando via watchdog" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Start-Process cmd -WorkingDirectory $base -WindowStyle Minimized -ArgumentList '/k', "title TRADING-BOT & set PYTHONIOENCODING=utf-8 & `"$py`" main.py"

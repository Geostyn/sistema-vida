# CLAUDE.md — Sistema de Trading XAUUSD

## ⚡ Ahorro de tokens (LEER PRIMERO)

1. **NO explores el código a ciegas.** El estado vivo del sistema está en
   `ESTADO-SISTEMA.md` del vault Obsidian (se inyecta solo por hook si el
   prompt menciona trading/MT5/señales/XAUUSD — si ves
   `[CONTEXTO TRADING AUTO-CARGADO]`, ya lo tienes, no lo releas).
2. **Usa Grep con patrones concretos**, no Read de archivos enteros
   (main.py 1500+ líneas, signal_engine.py 1700+ líneas).
3. **Mapa del código** (abajo) → ve directo al archivo correcto.

## Mapa del código

| Qué | Dónde |
|-----|-------|
| Loop principal, ciclo 60s, wiring de streams | `main.py` (run_cycle ~L1000) |
| Señales H1 intradía (16.5 confluencias) | `analysis/signal_engine.py` → `analyze()` |
| Señales SWING D1→H4 | `analysis/signal_engine.py` → `analyze_swing()` |
| Señales DAYTRADE M15 (manual, ilimitadas) | `analysis/signal_engine.py` → `analyze_daytrade()` |
| Cuenta FundedNext 2K (trailing DD 6%) | `risk/funded_account.py` |
| Cuenta personal micro (bloque MI CUENTA) | `risk/personal_account.py` |
| Formato Telegram + comandos | `alerts/telegram_bot.py` |
| Resolución WIN/LOSS/EXPIRED | `ml/outcome_tracker.py` (DAYTRADE→velas M15) |
| Backtest H1 validado | `backtest/backtester.py` |
| Backtest stream M15 | `backtest/daytrade_backtester.py` |
| Configuración completa | `config.yaml` (secciones: risk, funded, personal, daytrade, swing, liquidity) |
| Resultados | `logs/trades.db` (SQLite, tabla signals) |

## Comandos

```bash
# Python de producción (3.14) — el del PATH no tiene las dependencias
PY=/c/Users/geost/AppData/Local/Python/pythoncore-3.14-64/python.exe

$PY main.py --test                        # 1 ciclo y sale
$PY main.py --backtest                    # backtest H1 (filtros ON)
$PY backtest/daytrade_backtester.py       # backtest stream M15 (--days N)
# Arrancar/parar: INICIAR.bat / DETENER.bat (doble click)
```

MT5 debe estar abierto con AutoTrading en VERDE para ejecutar/backtestear.

## Arquitectura en 5 líneas

- 3 streams de señales en paralelo: INTRADAY H1 (auto-ejecutada en demo),
  DAYTRADE M15 (manual, alert-only, ilimitadas con dedup por contexto),
  SWING D1→H4 (manual, 1-3/semana).
- Cada señal Telegram lleva 2 bloques de cuenta manual: 💼 MI CUENTA
  (personal micro EUR, `/saldo` sync) y 🏦 FUNDEDNEXT 2K (`/equity` sync).
- Vetos de liquidez compartidos: sweep-contra, displacement, pool HTF intacto.

## Reglas

- Cambios de estrategia → validar con backtest ANTES de activar en vivo.
- El bot corre en vivo: tras editar código hay que reiniciar (DETENER + INICIAR).
- No tocar la lógica H1 validada (WR 42.1%, PF 1.61) sin re-backtest.
- Credenciales viven en `config.yaml` — no moverlas ni loguearlas.
- Escribir comentarios y mensajes en español, estilo del código existente.

# PLAN-FABLE — EJECUCIÓN (handoff actualizado 2026-07-04 ~23:45)

> **Para el asistente:** TODAS las fases del plan (A, B1-B8, C, D) están ejecutadas.
> Solo queda la **vigilancia P0** (48 h de mercado abierto desde el domingo 22:00 UTC)
> y decisiones del usuario (DAYTRADE off). Lee `PLAN-FABLE.md` (reglas) y este
> archivo (estado + resultados). Trabaja en español.

**Python de producción (siempre):**
`C:\Users\geost\AppData\Local\Python\pythoncore-3.14-64\python.exe` con `PYTHONIOENCODING=utf-8`.

**Regla de contención MT5:** el backtester unificado y `threshold_sweep`/`bias_audit`
corren 100% del caché en disco (`backtest/cache/`) — NO necesitan MT5 ni parar el bot.
Solo el preload y los backtests legacy (`main.py --backtest`, daytrade_backtester)
compiten con el bot.

---

## ✅ HECHO — sesión 2026-07-04 tarde (Fases A, B1-B6, commits `5a96306 → f6597b9`)

### Fase A — 7 fixes de salud (commit `6553b0d`)
SWING rama propia en outcome_tracker · categoría SCRATCH · CV temporal en learning_engine
· cooldown 5 min VP/intermarket · WAL + bot_meta · dedup_window 60 · reporte semanal gate UTC.

### Fase B1-B2 — HistoricalConnector + preload + reloj inyectable (`ec27886`)
- `backtest/historical_connector.py` + `backtest/preload_history.py` — caché
  2023-01-02 → 2026-07-03 en `backtest/cache/*.pkl`. Offset servidor↔UTC = +3 (verano).
- `SignalEngine(..., clock=)` y `MarketRegimeEngine(..., clock=)`.
- Selftest anti-lookahead: `preload_history.py --selftest` → OK.

### Fase B3-5 — Backtester unificado (`cffb35d`)
- `backtest/unified_backtester.py` — `analyze()` REAL barra a barra; correlation/regime/
  quant reales, news/macro mock, vp/delta/intermarket/ml/neural None → máx offline 12.3/16.5.
- ~8 min/año a cadencia H1.

### Fase B6 — Replay-validación (`f6597b9`)
- Componentes estructurales 100% match vs vivo; ideas reproducidas 67%.
- **GAP offline→vivo: Δ̄ = +0.74 → umbral_vivo ≈ umbral_offline + 0.7.**
  NUNCA copiar umbrales offline a config.yaml sin esta traducción.

---

## ✅ HECHO — sesión 2026-07-04 noche (Fases B7, B8, C, D)

### B7 — Barrido post-hoc de umbrales ✅ VEREDICTO: NO TOCAR min_confluences
- **Nuevo `backtest/threshold_sweep.py`**: re-simula la ejecución del `signals_log` de un
  run candidatos (`--min-confluences 0 --candidates`) por umbral t ∈ {4.0…8.0, 0.5},
  con las mismas puertas que run_unified y guardarraíles de `optimize.better()`.
- Runs candidatos: `unified_cand_is_*.json` (2025-07→2026-07, 266 señales) y
  `unified_cand_oos_*.json` (2024-07→2025-07, 316 señales).
- **IS**: t=5.5 gana (PF 2.00, ret 41.3%) pero **se cae en OOS** (PF 1.36 < baseline 1.42).
  t=7.0-7.5 sube PF en AMBAS (2.28/2.34 IS, 1.66/1.98 OOS) pero recorta retorno/trades
  → no pasa el guardarraíl ret≥+8pp. **Ningún umbral bate a 6.0 en ambas ventanas.**
- Verificación vs nativo: IS 83↔82 trades (±1 ✓), OOS 85↔83 (±2; efecto cadena-dedup
  documentado en el docstring — el signals_log a min_conf=0 tiene otra cadena `_is_duplicate`).
- JSON: `backtest/results/threshold_sweep_20260704_233250.json`.

### B8 — Baselines archivados (referencia permanente del engine real) ✅
| Ventana | Señales | Trades | WR | PF | Ret | MaxDD |
|---|---|---|---|---|---|---|
| IS 2025-07-04→2026-07-03 (`unified_baseline_is_*.json`) | 232 | 83 | 45.2% | 1.96 | +37.0% | 6.0% |
| OOS 2024-07-04→2025-07-04 (`unified_baseline_oos_*.json`) | 297 | 85 | 39.1% | 1.47 | +18.3% | 10.0% |

### Fase C — Sesgo direccional ✅ CAUSA RAÍZ: es el RÉGIMEN, no el código
- **Nuevo `backtest/bias_audit.py`** (punto-en-tiempo, sin MT5, --cadence H1/H4).
- **Jun-jul 2026** (ventana del 0-BUY): oro **-8.1%** (4540→4172); bias BEARISH en 530/545
  cierres H1 (0 BULLISH), `detect_htf_trend` = STRONG_DOWN el **100%** del tiempo → todo
  BUY habría sido vetado por counter_trend correctamente. Acierto forward del bias: 53.8%.
- **Año completo** (oro +25.3%): bias BULLISH 833 vs BEARISH 549 — **simétrico, sigue al
  mercado**. En los runs candidatos offline: IS emite **156 BUY vs 110 SELL**, OOS
  **262 BUY vs 54 SELL**. El 301-SELL/29-BUY del vivo = período bajista, no bug.
- BUY rinde mejor que SELL en ambas ventanas (WR 48/38% IS, 39/26% OOS) — coherente con
  el vivo. El gate que más mata es el **veto de liquidez** (BUY 1543 IS / 2278 OOS),
  proporcional al nº de candidatos; sin asesino oculto de BUYs.
- **Quick-win aplicado:** `signals` ahora persiste `dxy_aligned`, `mtf_aligned`,
  `macro_bias`, `tpo_score` (migración ALTER + INSERT en main.py; `tpo_score` añadido
  también al dict de señal en signal_engine — antes ni existía en el dict). Verificado
  con INSERT sintético + `--test` (migración aplicada a logs/trades.db).
- JSONs: `bias_audit_20260704_230335.json` (jun-jul H1), `bias_audit_20260704_*.json` (año H4).

### Fase D ✅
1. **`/ml_check` + auto-check semanal**: comando Telegram nuevo (telegram_bot.py + main.py)
   que corre `meta_labeling --dedup` en SUBPROCESO (el módulo hace os.chdir+logging.disable
   a nivel de import — no importable en el bot) y resume el veredicto. Gate automático:
   **sábado ≥10 UTC, 1×/semana ISO**, guard `last_ml_check` persistido en bot_meta.
   Ya disparó su primer informe (2026-W27) durante el --test.
2. **Auditoría DAYTRADE M15** (daytrade_backtester ganó `--from/--to`; corrido con MT5 y
   bot parado):
   - IS 2025-07→2026-07: 117 trades, WR 40.2%, PF 1.26, +19.2R, DD 8.1% → pasa.
   - **OOS 2024-07→2025-07: 138 trades, WR 36.2%, PF 1.01, +1.2R, DD 30.7%, equity -9.4%**
     → breakeven con drawdown brutal. Con el WR vivo dedup 28.6% (< breakeven 33.3%):
   - **PROPUESTA: `daytrade.enabled: false` — DECISIÓN DEL USUARIO (config NO tocado).**
     Logs: `logs/backtest_daytrade_is_XAUUSD.json` / `_oos_`.
3. **Dedup §9 medido** (DB vivo mayo-junio, pre-fix): de 87 velas con señales múltiples
   misma dirección, **27 tenían 2º setup a >0.75 ATR** (~13/mes suprimidos por el
   fingerprint). Patrón dominante: **ping-pong entre 2 OBs** (misma pareja de entries
   alternando hasta 10× en una vela — evade `_is_duplicate` porque solo compara con la
   ÚLTIMA señal). Eso es ruido que DEBE suprimirse → el fingerprint por vela se queda.

### Extra sesión noche
- **El bot estaba CAÍDO desde las 14:25** (log cortado a mitad de ciclo, sin evento de
  apagado del sistema — ventana cerrada). Reiniciado 23:34 (motor + dashboard headless,
  MT5 abierto). ⚠️ Verificar AutoTrading VERDE antes del domingo 22:00 UTC.

---

## ⬜ PENDIENTE

### P0 — Vigilancia dedup (48 h de mercado abierto)
Mercado cerrado desde el 03-07 (festivo US + finde); reabre **domingo 22:00 UTC**.
Comprobado 04-07 23:00: 0 señales nuevas desde el 04-07 (esperado). Con las primeras
señales de la semana:
```sql
SELECT strftime('%Y-%m-%dT%H', timestamp) bar, direction, COUNT(*) n
FROM signals WHERE timestamp >= '2026-07-04' GROUP BY 1,2 HAVING n > 1;
```
Aceptación: 0 filas en 48 h de mercado abierto. También revisar `sent_signals`.

### Decisiones del usuario
1. ~~DAYTRADE M15~~ → **DESACTIVADO 2026-07-04 ~23:45** (el usuario delegó "haz lo
   que sea mejor"): `daytrade.enabled: false` con justificación en config.yaml.
   Reactivar solo si un re-backtest futuro pasa IS+OOS.
2. Revisar el primer informe `/ml_check` en Telegram (llegará cada sábado).

### Mejoras operativas añadidas (2026-07-04 ~23:45)
- **WATCHDOG del bot** (`watchdog.ps1` + tarea Windows `TradingBotWatchdog`, cada
  10 min): si `main.py` (python de producción) no corre, lo relanza minimizado y
  lo apunta en `logs/watchdog.log`. **Probado end-to-end** (matado el bot → el
  watchdog lo revivió en <10 s). Apagado intencionado: `DETENER.bat` crea
  `logs/watchdog.pause` (el watchdog NO relanza); `INICIAR.bat` la borra.
  Gestión: `schtasks /Query /TN TradingBotWatchdog` · `/Delete ... /F` para quitar.
  ⚠️ **Fix 2026-07-05:** la tarea creada con `schtasks /Create` quedó ROTA
  (escapado de comillas corrompió la ruta del .ps1 → fallo -196608). Recreada con
  `Register-ScheduledTask` nativo (+ StartWhenAvailable, AllowStartIfOnBatteries,
  límite 5 min). Receta completa en el header de `watchdog.ps1`.
  Disparo programado verificado OK (00:17:54, resultado 0), PERO la tarea se
  **auto-deshabilitó 2 veces** (sin detección Defender; log TaskScheduler apagado).
  Re-habilitada + vigilancia 25 min en curso. **SI VUELVE A DESHABILITARSE:**
  plan B = watcher persistente en shell:startup (bucle PowerShell cada 10 min),
  sin Task Scheduler. Comprobar en la próxima sesión:
  `(Get-ScheduledTask -TaskName TradingBotWatchdog).State` debe ser Ready.
- **Ablación de confluencias** (`backtest/confluence_report.py` NUEVO, read-only,
  sobre los trades ejecutados de los runs cand IS+OOS, n≈70/ventana):
  - `m15_aligned` **AYUDA en ambas** (+21% / +6% WR) · `rsi_extremo` **AYUDA en
    ambas** (+7% / +17%) → confluencias con señal real.
  - `dxy_aligned` **ESTORBA en ambas** (−4% / −10%, n✓ 17/21 pequeño) → único
    candidato a experimento de pesos futuro; exige validación 2 ventanas antes
    de tocar nada (NO aplicado).
  - **EXPERIMENTO EJECUTADO (2026-07-05, pedido del usuario): NO-GO.** Knob nuevo
    `correlation.dxy_confluence_weight` (default 1.5 = idéntico) + `--set` genérico
    en unified_backtester. Peso 0 en 2 ventanas: IS PF 1.96→2.02 ✓ pero
    **OOS PF 1.47→1.41 / ret 18.3→14.9% / DD 10→12.3% ✗✗✗** → curve-fitting,
    descartado (añadido a trampas §4 de PLAN-FABLE.md). El peso 1.5 SE QUEDA.
    JSONs: `unified_dxy0_is_*.json` / `unified_dxy0_oos_*.json`.
  - corr(confluences, pnl_r) = **+0.10 IS / +0.16 OOS** — el score total predice
    débil pero positivo (coherente con el barrido: umbral alto ⇒ PF alto).
  - Resto (sweep, fvg, pairs, regime, adx) = mixto/neutro entre ventanas = ruido
    o muestra corta. No tocar pesos con esta n.

### Ideas para próximas sesiones (no urgente)
- El único patrón interesante del barrido: t≥7.0 offline (≈7.7 vivo) sube PF en ambas
  ventanas a costa de frecuencia — si algún día se quiere un modo "calidad" (menos
  señales, más certeras), ahí hay señal real. NO pasa los guardarraíles actuales.
- Cuando haya ~150 trades limpios multi-régimen: re-evaluar ML con `/ml_check`.
- El veto de liquidez es el gate dominante (2400+ descartes/año) — candidato a
  auditoría fina con el unified si se busca más frecuencia.

## Notas operativas
- Comandos del backtester unificado: ver bloque en la sección B3-5 de la sesión tarde
  (sin cambios). Nuevos: `threshold_sweep.py --cand-is ... --cand-oos ...` y
  `bias_audit.py --from ... --to ... [--cadence H4]`.
- daytrade_backtester acepta ahora `--from YYYY-MM-DD --to YYYY-MM-DD` (usa MT5 →
  bot parado).
- El digest de --test manda "SISTEMA ACTIVO" a Telegram — normal, no es el bot vivo.
- IDE marca "Cannot find module MetaTrader5/schedule" — intérprete del IDE distinto;
  el de producción compila todo OK. Ignorar.

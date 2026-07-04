# PLAN-FABLE — EJECUCIÓN (handoff actualizado 2026-07-04 ~15:00)

> **Para el asistente:** la sesión del 2026-07-04 ejecutó las Fases A, B1-B6 completas
> (commits `5a96306 → f6597b9` en este repo). Lee `PLAN-FABLE.md` (reglas) y este archivo
> (estado + qué falta). **Empieza por la FASE B7** (barrido de umbrales) o por donde pida
> el usuario. Trabaja en español.

**Python de producción (siempre):**
`C:\Users\geost\AppData\Local\Python\pythoncore-3.14-64\python.exe` con `PYTHONIOENCODING=utf-8`.

**Regla de contención MT5 que CAMBIÓ:** el backtester unificado corre 100% del caché en
disco (`backtest/cache/`) — NO necesita MT5 ni parar el bot. Solo el preload y los
backtests legacy (`main.py --backtest`, daytrade_backtester) compiten con el bot.

---

## ✅ HECHO (2026-07-04)

### Fase A — 7 fixes de salud (commit `6553b0d`) — bot ya corriendo con esto
1. SWING con rama propia en outcome_tracker (velas H4, `swing.max_outcome_bars_h4: 60`)
   + `"model": "SWING"` en el dict de señal (signal_engine ~L1270).
2. Categoría **SCRATCH** para cierres ~0 por auto-BE (|pnl| ≤ 1.5×comisión×volumen) —
   excluida del WR como EXPIRED. Lectores actualizados: `get_performance_stats`,
   `main._get_trade_stats`, ESTADO-SISTEMA, /status, digest, demo log Obsidian.
3. CV temporal en learning_engine: `TimeSeriesSplit` + orden GLOBAL por timestamp
   (vivo+backtest mezclados; training_store ahora devuelve el timestamp de columna).
4. Cooldown de fallo 5 min (patrón macro_feed) en volume_profile y las 4 fuentes de
   intermarket_feed + timeout COT 45→15 s.
5. `PRAGMA journal_mode=WAL` + `busy_timeout=5000` en init_database + tabla `bot_meta`.
6. `alerts.dedup_window_min: 45 → 60`.
7. Reporte semanal con gate UTC propio (`_maybe_send_weekly_report`, domingo ≥18 UTC,
   semana ISO) + guards de digest/semanal **persistidos** en `bot_meta`.

Verificación: `main.py --test` OK ×2. **Bot reiniciado 04-07 14:01** vía INICIAR.bat.

### Fase B1 — HistoricalConnector + preload (commit `ec27886`)
- `backtest/historical_connector.py` — duck-type de MT5Connector: cursor `set_now()`,
  slices point-in-time O(log n), velas H4/D1 **y H1** en formación sintetizadas desde
  el sub-TF (H1←M15, H4/D1←H1), bid/ask sintético (spread 0.30 config), `LookaheadError`.
- `backtest/preload_history.py` — caché descargado: XAUUSD M15/H1/H4/D1 + 6 pares DXY,
  **2023-01-02 → 2026-07-03** en `backtest/cache/*.pkl` + `meta.json`.
- **Offset servidor↔UTC = +3** (verano; EET +2 invierno). Medido de los DATOS (apertura
  semanal Lun 01:00 server = Dom 22:00 UTC Globex) porque el 04-07 era festivo US sin
  ticks frescos. Caveat DST ±1h documentado en meta.json.
- Selftest anti-lookahead: `preload_history.py --selftest` → OK (25 cursores × 11 series).

### Fase B2 — Reloj inyectable (mismo commit)
- `SignalEngine.__init__(..., clock=None)` → `self._now()`; 16 ocurrencias sustituidas
  (`grep -c "datetime.now" signal_engine.py` == 1). Igual en `MarketRegimeEngine`.
  Default idéntico → vivo bit a bit igual (verificado --test).

### Fase B3-5 — Backtester unificado (commit `cffb35d`)
- `backtest/neutral_mocks.py` — news neutro (+1.0 fija), macro neutro (+0.3 fija).
- `backtest/unified_backtester.py` — `SignalEngine.analyze()` REAL barra a barra.
  Wiring espejo de main.py: correlation REAL (memoizada por vela H4), regime y quant
  REALES (garch_cache_min=0 por overlay); vp/delta/intermarket/ml/neural = None
  → **máx offline 12.3/16.5**. Señales 24h; ejecución simulada con gates de sesión
  (UTC desde offset) y max_simultaneous; reutiliza `_simulate_trade_managed` y
  `_calculate_metrics` del legacy (comisiones incluidas).
- Rendimiento: **~11 min/año a cadencia H1 sin stride** (objetivo <40 ✓).
- Verificado: 30 días → 19 señales, 6 trades, WR 25% PF 0.59 (junio fue malo también
  en vivo); 3 señales auditadas a mano (18/18 checks).

### Fase B6 — Replay-validación (commit `f6597b9`)
- `backtest/replay_validate.py` + `--cadence M15` + `discard_log` por barra.
- Señales reales jun-jul: 254 filas OB → 195 dedup por vela → **43 ideas** (regla
  `_is_duplicate` del engine: misma dirección + entry <0.75 ATR).
- **RESULTADO:** componentes estructurales **100%** (bias_h4, ob_type, regime, adx±2,
  sweep, fvg — 16/16); atr±5% 81% y m15 88% = efecto forming-bar (documentado, NO
  lookahead). Match estricto 37%; **ideas reproducidas 67%** (match + 13 casos donde
  el dedup del engine emitió la idea antes). 100% de no-match con causa asignable
  (vetos de liquidez/HIGH_VOL sensibles al instante de muestreo: vivo evalúa cada 60 s).
- **GAP offline→vivo: Δ̄ = +0.74, σ = 0.58** (ML +0.19, residual macro+intermarket
  +0.56) → **umbral_vivo ≈ umbral_offline + 0.7**. NUNCA copiar umbrales offline a
  config.yaml sin esta traducción. Informe: `backtest/results/replay_validation.json`.

### Comandos del backtester unificado (sin MT5, con el bot vivo)
```bash
PY=/c/Users/geost/AppData/Local/Python/pythoncore-3.14-64/python.exe
$PY backtest/unified_backtester.py --days 30 --discards          # run normal
$PY backtest/unified_backtester.py --days 365 --min-confluences 0 --candidates --tag cand_is
$PY backtest/unified_backtester.py --from 2024-07-04 --to 2025-07-04 --tag oos
$PY backtest/replay_validate.py --from 2026-06-03 --to 2026-07-03
$PY backtest/preload_history.py --selftest                       # validar caché
# refrescar caché (SOLO con bot parado): $PY backtest/preload_history.py --refresh
```

---

## ⬜ PENDIENTE (en orden)

### B7 — Barrido post-hoc de umbrales
1 run candidatos 1 año (`--min-confluences 0 --candidates --tag cand_is`, cadencia H1)
→ del `signals_log` del JSON, simular por umbral t ∈ {4.0…8.0 paso 0.5}: filtrar señales
`confluences ≥ t` + re-simular ejecución (gates sesión/max_sim) — o más simple: correr
2-3 umbrales de control nativos y verificar ±1 trade. Recordar: escala OFFLINE
(vivo ≈ offline + 0.7). Guardarraíles `optimize.better()` (PF≥+0.05, ret≥+8pp,
DD≤+2pp, ≥70% trades) en AMBAS ventanas.

### B8 — Baseline IS/OOS archivado
- IS:  `--from 2025-07-04 --to 2026-07-03 --tag baseline_is`  (~11-15 min)
- OOS: `--from 2024-07-04 --to 2025-07-04 --tag baseline_oos`
- Guardar los JSON de results/ como referencia permanente (baseline del engine real).

### Fase C — Sesgo direccional (P2: 0 BUY desde 01-06)
1. `compute_directional_bias(df_h4)` sobre jun-jul con el HistoricalConnector:
   distribución BULLISH/BEARISH/NEUTRAL por día vs precio real del oro. (El replay
   confirmó que el bias offline == vivo al 100% → el análisis offline VALE.)
   Dato: junio fue TRENDING_DOWN casi todo el mes (regime 100% match) — puede que el
   0-BUY sea correcto (oro bajista), verificarlo contra el precio.
2. Funnel BUY: % de candidatos BUY muertos por gate — usar `--candidates --discards`
   (el discard_log ya registra motivo por barra).
3. Quick-win: persistir en `signals` las confluencias no guardadas (dxy_aligned,
   mtf_aligned, macro_bias, tpo_score...) — migración ALTER en init_database
   (patrón existente), INSERT en main.save_signal ~L230.
4. Informe causa raíz. NO tocar el bias sin validación 2 ventanas.

### Fase D — Resto P3
1. Comando Telegram `/ml_check` + recordatorio semanal (sábado, mercado cerrado) para
   `meta_labeling --dedup`. Guardarraíl: expectancia OOS >0.05R manteniendo ≥40% señales.
2. Auditoría DAYTRADE M15 (vivo dedup WR 28.6% vs backtest 41%): re-backtest 2 ventanas
   con `daytrade_backtester.py` (⚠️ usa MT5 → bot parado o adaptarlo al connector).
   Si no pasa → proponer `daytrade.enabled: false` (decisión del usuario).
3. Dedup §9: contar velas H1 con 2 setups legítimos distintos misma dirección (~0 esperado).

### P0 — Vigilancia dedup (48 h)
Bot corriendo desde 04-07 14:01 con dedup_window_min=60. **04-07 festivo US → mercado
cerrado; reabre domingo 22:00 UTC.** Con las primeras señales de la semana:
```sql
SELECT strftime('%Y-%m-%dT%H', timestamp) bar, direction, COUNT(*) n
FROM signals WHERE timestamp >= '2026-07-04' GROUP BY 1,2 HAVING n > 1;
```
Aceptación: 0 filas en 48 h de mercado abierto. También revisar `sent_signals`.

### Documentación final
- `PLAN-FABLE.md` → actualizar estado (fases completadas).
- Vault: `ideas/mejoras-sistema-trading.md` (backlog con /obsidian-markdown).
- Memoria de Claude (project_trading).
- `ESTADO-SISTEMA.md` NO se toca (auto-generado).

## Notas operativas de la sesión 04-07
- El bot estaba CAÍDO desde el 03-07 22:20 (apagón del PC, log cortado a mitad de
  ciclo). MT5 también cerrado; `mt5.initialize()` lo relanza solo.
- El PC estaba con racha de 4 pérdidas y límite DD semanal (training_mode sigue
  ejecutando). Junio fue un mes malo también en el unified (WR 25% PF 0.59).
- El digest de --test manda "SISTEMA ACTIVO" a Telegram — normal, no es el bot vivo.
- IDE marca "Cannot find module MetaTrader5/schedule" — intérprete del IDE distinto;
  el de producción compila todo OK. Ignorar.

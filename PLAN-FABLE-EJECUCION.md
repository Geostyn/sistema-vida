# PLAN-FABLE — EJECUCIÓN (handoff para la próxima sesión)

> **Para el asistente:** la sesión del 2026-07-03 completó TODA la exploración y el diseño
> (plan aprobado por el usuario) pero NO ejecutó ningún cambio — se acabaron los créditos justo
> tras la aprobación. Lee `PLAN-FABLE.md` (contexto y reglas) y luego este archivo (plan aprobado
> + hallazgos verificados). Empieza directamente por la FASE A. Trabaja en español.

**Python de producción (siempre):** `C:\Users\geost\AppData\Local\Python\pythoncore-3.14-64\python.exe` con `PYTHONIOENCODING=utf-8`. No correr backtests con `main.py` vivo (contención MT5: parar→backtest→reiniciar).

---

## Estado exacto al cortarse la sesión

- **NADA ejecutado aún.** Ni commit de seguridad ni ningún fix. El primer paso pendiente es el
  snapshot git (`git add -A && git commit`) — ojo: hay ~11 archivos modificados y ~9 nuevos sin
  commitear de la sesión anterior (dedup, digest, yf_safe...); el snapshot los captura tal cual.
- **P0 (dedup):** bot reiniciado 03-07 13:04 con el código nuevo (signal_dedup.py 11:21). Los 20
  duplicados de la DB son del 02-07 11:33–11:39 (ANTES del fix). `sent_signals` = 0 filas = ninguna
  señal emitida desde el reinicio. **Pendiente:** confirmar con la primera señal real + 48 h sin duplicados.
- **Descubrimiento clave (P2, peor de lo documentado):** las 29 BUY de la DB son TODAS del
  29-05→01-06 (código antiguo: scores NULL, confluencias 3–4.8, 22 son pares duplicados WIN+LOSS
  al mismo minuto). **Desde el 01-06: 0 BUY y 320 SELL.** `bias_h4`=BEARISH en el 100% de las señales
  del código actual. La pregunta de P2 no es "por qué BUY gana más" sino "por qué el pipeline NUNCA produce BUY".
- `config.yaml` está en `.gitignore` y NO versionado (verificado — la alerta de seguridad no requiere acción).
- Esquema `signals`: 62 columnas; el stream se distingue por `model` ('OB' intraday, 'DAYTRADE'; swing
  NO pone model → bug 1). DAYTRADE en vivo: 15 señales, 4W/11L.

---

## FASE A — Fixes de salud (7 bugs confirmados por auditoría) — 1 reinicio del bot

Quirúrgicos, sin tocar la estrategia H1 validada. Commit de seguridad ANTES, commit por fase.

1. **`ml/outcome_tracker.py:158` — SWING sin rama propia.** `is_dt=(model=="DAYTRADE")` pero swing pone
   `"mode":"SWING"` (signal_engine.py:1269) sin `model` → default `"OB"` (main.py:279) → se resuelve en
   H1/150 barras (~6 días) < hold swing 2–7 días → EXPIRED prematuro. Fix: `"model":"SWING"` en el dict
   swing + rama en el tracker (velas H4, horizonte desde `swing.*`). Solo afecta señales nuevas.
2. **`ml/outcome_tracker.py::_detect_outcome_mt5` — BE cuenta como LOSS** (`pnl>0?WIN:LOSS`): scratch
   por auto-BE cierra ~0 negativo por comisiones → infla LOSS. Fix: categoría `SCRATCH`
   (|pnl| ≤ ~1.5× comisión), excluida del WR como EXPIRED (el backtest ya tiene SCRATCH). Actualizar
   lectores: `get_performance_stats`, `main._get_trade_stats`, dashboard, digest.
3. **`ml/learning_engine.py:300` — CV con fuga.** `cross_val_score(cv=int)`=StratifiedKFold. Fix:
   `TimeSeriesSplit(n_splits=cv_folds)` **y antes ordenar por timestamp** el dataset combinado
   (`_load_training_data` ~218-229 concatena backtest DESPUÉS del vivo, sin orden global).
4. **Cooldown de fallo ausente:** portar patrón `FAIL_COOLDOWN_MIN=5` de `data/macro_feed.py` a
   `analysis/volume_profile.py::get_levels` (hoy: caché stale sin renovar `_cache_time` → refetch cada
   5 s en caídas) y a las 4 fuentes de `data/intermarket_feed.py`. **COT además:** `requests.get(timeout=45)`×3
   síncrono sin yf_safe → puede bloquear el ciclo ~135 s; bajar timeout y cooldown propio.
5. **`main.py::init_database` — `PRAGMA journal_mode=WAL` + `busy_timeout`** (hoy sin WAL; bot escribe + dashboard/skills leen).
6. **`config.yaml: alerts.dedup_window_min: 45 → 60`** (ventana < vela H1; garantiza 1 envío/vela duro).
7. **`main.py` — bases de tiempo:** reporte semanal `schedule.every().sunday.at("18:00")` = hora LOCAL
   vs digest UTC → pasarlo a gate UTC (patrón `_maybe_send_daily_digest`) + **persistir** `_last_digest_date`
   (hoy en memoria → reenvía el digest si el bot reinicia después de las 21:00 UTC).

**Verificación:** `main.py --test` (1 ciclo, MT5 abierto) → reiniciar bot (DETENER.bat/INICIAR.bat) → log de arranque limpio. P0: vigilar 48 h sin duplicados (SQL por vela).

---

## FASE B — Backtester unificado (P1, EL desbloqueo)

Objetivo: correr el `SignalEngine.analyze()` REAL barra a barra sobre histórico → `min_confluences`, ML, LSTM y pesos pasan a ser validables. Viable: todo se inyecta ya por constructor (`SignalEngine.__init__(mt5_connector, news_feed, config, correlation_engine, macro_feed, learning_engine, volume_profile, delta_engine, regime_engine, intermarket_feed, neural_engine, quant_engine)`); los datos entran por `self.mt5.get_rates()`/`get_current_price()` (helper `_get_data` ~L741).

**Decisiones de diseño aprobadas:**
- Evaluación al cierre de cada barra H1; velas H4/D1 **en formación sintetizadas** desde H1 (sin esto el bias diverge 3 de cada 4 horas).
- Mocks neutros constantes: news (+1.0), macro NEUTRAL (+0.3) — no distorsionan ranking. VP/delta/intermarket = None en v1 → **máximo offline 12.3/16.5**. El gap variable se MIDE con replay contra `trades.db`, no se asume. Barridos en escala offline; traducción a vivo con gap medio Δ̄ — NUNCA copiar el umbral directo a config.yaml.
- Correlation (DXY sintético desde 6 pares MT5), regime y quant van REALES con el connector histórico.

**Archivos nuevos:**
- `backtest/historical_connector.py` — duck-type de `MT5Connector` (`get_rates`, `get_current_price`, `copy_ticks_range`→None, `get_symbol_info`) con cursor `set_now(t)`; slices point-in-time O(1) con `np.searchsorted` (patrón de daytrade_backtester L163/223); síntesis de HTF en formación; bid/ask sintético (spread XAUUSD ~0.30 configurable); asserts `LookaheadError`; precarga 1 vez por (símbolo,TF) → pickle en `backtest/cache/` + `meta.json` (offset servidor↔UTC, cobertura). Símbolos: XAUUSD (M15/H1/H4/D1 + warm-up) y los 6 pares DXY (EURUSD/USDJPY/GBPUSD H1+H4; USDCAD/USDSEK/USDCHF H1).
- `backtest/neutral_mocks.py` — NewsNeutralMock (`is_news_blackout`→False, `get_daily_summary`→[]), MacroNeutralMock (`get_macro_bias`→NEUTRAL). ~40 líneas.
- `backtest/unified_backtester.py` — `build_backtest_engine()` espejo del wiring de main.py (~L643-652); bucle H1 con gates de sesión/max_simultaneous como el vivo (réplicas de `risk_manager.is_session_allowed` y `can_open_trade`); **REUTILIZA `_simulate_trade_managed` (backtester.py:561) y `_calculate_metrics` (:679)** — no duplicar. Modo candidatos: run con `min_confluences=0` registrando candidato+umbral efectivo → barrido post-hoc de N umbrales con 1 run (verificar con 2 umbrales de control nativos, ±1 trade). CLI: `--symbol --days --from/--to --refresh-cache --stride-garch --min-confluences`.
- `backtest/replay_validate.py` — meta-validación: replay jun–jul 2026 vs señales reales de `trades.db`. Matching: misma dirección, ±75 min, entry ±0.5 ATR. Componentes reproducibles (bias_h4, ob_type, regime, adx±2, sweep/fvg/m15/pairs, atr±5%) deben coincidir ≥95% — desviación = BUG (lookahead/TZ/forming-bar), no efecto de mocks. Publicar Δ̄/σ del gap de confluencias (descomponer con vp_score/delta_score de la DB). Aceptación: ≥70-80% matcheadas, 100% de no-match con causa asignable.

**Único cambio de producción — reloj inyectable:**
- `SignalEngine.__init__`: kwarg `clock=None` → `self._now = clock or (lambda: datetime.now(timezone.utc))`.
- Sustituir las **16 ocurrencias** de `datetime.now(timezone.utc)` por `self._now()` en signal_engine.py: L88, 554, 555, 574, 714, 1199, 1200, 1211, 1297, 1324, 1376, 1379, 1429, 1750, 1781, 2065 (líneas del 03-07 — reverificar con Grep).
- Igual en `analysis/market_regime.py` (kwarg clock; usos en `_cache_valid` L176 y `_cache_time` L200).
- Verificación: `grep -c "datetime.now" signal_engine.py` == 1 + import-check. Default idéntico → vivo bit a bit igual.
- Cachés restantes: `quant.garch_cache_min=0` vía overlay de config en memoria (deepcopy; config.yaml intacto). Instancia fresca de engine por run (resetea dedupe en memoria).

**Secuencia:** (1) connector+preload+selftest anti-lookahead (bot parado SOLO en preload) → (2) reloj → (3) mocks+wiring, analyze() en 5 cursores → (4) bucle 30 días, auditar 3 señales a mano → (5) rendimiento (objetivo año fiel <40 min; cuello=GARCH; `CachedCorrelationEngine` memoiza `get_pair_alignment` por vela H4) → (6) replay-validación → (7) modo candidatos+barrido → (8) doble ventana IS/OOS (integrar con validate_change) + archivar baseline.

**Riesgos:** lookahead (asserts + "PF demasiado bueno = sospechar"), DST servidor (offset en meta.json, ±1h documentado), GIGO pares DXY (preflight cobertura; sin cesta completa cae al fallback base-100 ≠ vivo), sensibilidad spread s∈{0.20,0.30,0.40}.

---

## FASE C — Sesgo direccional (P2)

1. Con el HistoricalConnector: distribución BULLISH/BEARISH/NEUTRAL de `compute_directional_bias(df_h4)` (`analysis/trend_filter.py`) por día de jun–jul vs precio real del oro. ¿Oro realmente bajista o bias sesgado/lagueado?
2. % de candidatos BUY muertos en cada gate (counter_trend_veto, OB alineado, sweeps) con el modo candidatos + `last_discard`.
3. Quick-win: persistir en `signals` las confluencias hoy NO guardadas: `dxy_aligned`, `mtf_aligned`, `macro_bias`, componentes RSI/OB/news/estructura, `tpo_score` (migración ALTER en `init_database`; el INSERT está en `main.save_signal` ~L218).
4. Informe causa raíz + corrección SOLO si valida en 2 años con el backtester unificado. NO tocar el bias en vivo sin eso.

## FASE D — Resto P3

1. Job ML semanal: `meta_labeling --dedup` NO puede correr con el bot vivo (MT5) → correrlo el sábado (mercado cerrado) vía script/recordatorio Telegram; avisar cuando pase el guardarraíl (expectancia OOS >0.05R manteniendo ≥40% señales).
2. Auditoría DAYTRADE M15 (vivo dedup WR 28.6% vs backtest 41%): re-backtest `daytrade_backtester.py` en 2 ventanas; si no pasa guardarraíles → proponer `daytrade.enabled: false` (informar al usuario antes).
3. Medir falsos positivos del dedup (§9 PLAN-FABLE): ¿cuántas velas H1 tienen 2 setups legítimos misma dirección? (probablemente ~0).

## Reglas de cierre

- Todo cambio de estrategia: doble ventana (IS 365→0 + OOS 730→365) + guardarraíles `optimize.better()` (PF≥+0.05, ret≥+8pp, DD≤+2pp, ≥70% trades). Lo que no pase → documentar y descartar.
- Al final: actualizar `PLAN-FABLE.md` (estado), backlog del vault `ideas/mejoras-sistema-trading.md` (con /obsidian-markdown), memoria de Claude. `ESTADO-SISTEMA.md` NO se toca (auto-generado).
- Plan completo aprobado también en: `C:\Users\geost\.claude\plans\aqui-esta-todo-docuemntado-effervescent-parrot.md`

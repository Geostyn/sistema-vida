# PLAN DE MEJORA — Bot de Trading XAUUSD (handoff para otra sesión / otro modelo)

> **Para el asistente que lea esto:** eres un ingeniero cuantitativo senior. Este bot
> FUNCIONA y tiene dinero (demo) en juego. Tu trabajo NO es reescribirlo: es auditarlo,
> encontrar fallos reales y mejorar SOLO lo que valides con datos. Lee entero este
> documento ANTES de tocar nada. Trabaja en español (el dueño es hispanohablante).

---

## 0. Cómo sacarle el máximo a esta sesión (modo recomendado)

- **Empieza en PLAN MODE.** Que el modelo explore y proponga un plan ANTES de editar. Este
  sistema es delicado; un cambio a ciegas puede romper un edge que costó semanas.
- **Máximo razonamiento / thinking.** Pide "piensa a fondo" (extended thinking / reasoning
  effort alto en `/config`). Los problemas buenos aquí son de razonamiento, no de teclear.
- **NO uses Fast mode** para esto (prioriza velocidad sobre profundidad; además es solo Opus).
- El valor no está en el modelo concreto sino en el PROCESO: plan → explorar → hipótesis →
  backtest in-sample + out-of-sample → aplicar solo lo robusto. Este documento te da los
  criterios de aceptación para que no te engañes con overfitting.
- Verifica cada afirmación contra el código actual (este doc es una foto del 2026-07-03;
  las líneas exactas pueden haber cambiado — usa Grep con patrones, no números de línea).

---

## 1. Contexto mínimo (arranque en frío)

- **Código:** `C:\Users\geost\Desktop\trading-system\`
- **Runtime Python del bot (IMPORTANTE, no el del PATH):**
  `C:\Users\geost\AppData\Local\Python\pythoncore-3.14-64\python.exe`
  Tiene MT5, torch 2.12, scikit-learn, yfinance, fredapi, groq, etc. El `python` del shell
  es OTRA instalación sin esas deps → usa SIEMPRE la ruta completa de arriba.
- **Arrancar (usuario):** doble click `INICIAR.bat` (llama a `launch.py`). Requiere MT5 abierto
  con AutoTrading en VERDE. Dashboard en http://127.0.0.1:8501.
- **Parar:** `DETENER.bat`.
- **Reinicio quirúrgico de solo el motor** (PowerShell): localizar `python.exe` cuyo
  CommandLine casa `\bmain\.py` → `Stop-Process` → relanzar
  `cmd /k "set PYTHONIOENCODING=utf-8 & cd /d BASE & <PY> main.py"`.
- **Mapa del código:** `CLAUDE.md` en esta misma carpeta.
- **Base de datos:** `logs/trades.db` (SQLite). Tabla `signals` = cada señal + `outcome`
  (WIN/LOSS/EXPIRED/PENDING) + features. Tabla `sent_signals` = dedup persistente (nuevo).

### Comandos clave
```bash
PY="C:/Users/geost/AppData/Local/Python/pythoncore-3.14-64/python.exe"
cd "C:/Users/geost/Desktop/trading-system"
PYTHONIOENCODING=utf-8 "$PY" main.py --backtest            # backtest H1 1 año
PYTHONIOENCODING=utf-8 "$PY" backtest/diagnose.py          # autopsia de trades reales
PYTHONIOENCODING=utf-8 "$PY" backtest/study_hours.py       # estudio de horas (2 años)
PYTHONIOENCODING=utf-8 "$PY" backtest/optimize.py          # sweep gestión (SL/RR/parcial/trail)
PYTHONIOENCODING=utf-8 "$PY" backtest/validate_change.py   # validación OOS año-anterior
PYTHONIOENCODING=utf-8 "$PY" backtest/meta_labeling.py --dedup   # gatekeeper ML walk-forward
PYTHONIOENCODING=utf-8 "$PY" backtest/eval_ml.py           # eval walk-forward del ensemble
PYTHONIOENCODING=utf-8 "$PY" ml/neural_engine.py --train --days 900   # entrenar LSTM
PYTHONIOENCODING=utf-8 "$PY" backtest/neural_eval.py       # validar LSTM OOS
```

---

## 2. Reglas de disciplina — INNEGOCIABLES

1. **Nada de estrategia se activa sin backtest robusto in-sample + out-of-sample (2 años).**
   Un cambio solo se aplica si mejora PF/Retorno **SIN subir el Max Drawdown en AMBOS años**.
   Lo que solo gana in-sample = curve-fitting → descartar.
2. **NO corras backtests con el bot vivo (`main.py`) activo.** MT5 es UN solo terminal; la
   contención CUELGA el backtest. Patrón: parar main.py → backtest → reiniciar.
3. **Todo lo nuevo entra con flag APAGADO por defecto** (como `neural.influence=false`,
   `ml.vote_enabled=false`). Se activa solo tras validar.
4. **Cuenta demo.** Puedes experimentar, pero el objetivo es un edge REAL, no números bonitos.
5. **`ESTADO-SISTEMA.md` lo regenera el bot solo** — no editarlo a mano. Notas durables van a
   `ideas/mejoras-sistema-trading.md` del vault Obsidian y a la memoria de Claude.

---

## 3. Estado actual (2026-07-03)

- **Edge real (deduplicado):** WR 44.6% global, modelo H1/OB **45.6%**, coincide con backtest
  (PF 1.5–1.77 OOS en 2 años). El sistema NO está roto.
- **Solo opera XAUUSD** (forex/plata/índices probados y descartados: no generalizan).
- **Gestión:** entry LIMIT en retroceso a OB, BE@1R, parcial 30% en TP1, trailing 2.0×ATR a TP2.
  Confirmada óptima por `optimize.py` (ningún knob la supera).
- **Sesión 7–21 UTC**, hora 16 bloqueada (validado). **El filtro horario está agotado**
  (ninguna otra ventana bate a 7-21 en ambos años — re-verificado 2026-07-03).
- **ML ensemble (RF+GBM+ET+MLP):** `vote_enabled=false`. **LSTM:** `influence=false` (modo sombra).
  Ambos apagados porque no tienen edge OOS (ver §5).

### Hecho esta sesión (2026-07-03)
- **Fix del spam de Telegram** = deduplicador persistente `alerts/signal_dedup.py` (tabla
  `sent_signals`, sobrevive reinicios, cubre 3 streams). Descubrimiento: **el 24% de los
  trades en la DB eran DUPLICADOS** (un setup emitido 10× en una vela) → inflaban el loss count.
- **Robustez:** `data/yf_safe.py` (yfinance con timeout/retry), caché VP COMEX a disco, índices
  SQLite, `requirements.txt` completo, comando Telegram `/salud`, fix de rate-limit (cooldown
  de 5 min en macro_feed para no martillear a Yahoo).
- **Digest diario con LLM** `alerts/daily_digest.py` (Groq, 21:00 UTC).
- **Dedup del entrenamiento ML** en `learning_engine._load_training_data` (entrena limpio).
- **meta_labeling.py** gana flag `--dedup`.

---

## 4. TRAMPAS CONOCIDAS — no vuelvas a caer (ya probado y descartado)

| Idea | Veredicto | Por qué |
|---|---|---|
| Bloquear más horas (7-11) | ❌ NO | Overfit: buenas en Y1, malas en Y2. Es régimen, no hora. |
| `min_rr=2.5` "gana" en backtest | ❌ TRAMPA | El backtester lo usa como objetivo de TP (semántica opuesta al vivo, que lo usa como filtro). |
| Veto ADX contra-tendencia | ❌ Redundante | `counter_trend_veto` ya bloquea el 100% de eso. |
| Reactivar ML / veto contrario ml_proba | ❌ NO | La "inversión r=−0.349" era ARTEFACTO de los duplicados; en walk-forward limpio el ML es casi aleatorio. |
| VWAP como señal | ❌ Sin edge | PF<1 ambos años. |
| ORB/breakout standalone | ❌ No robusto | Gana en tendencia, pierde en rango (espejo del bot). |
| Plata / forex / índices | ❌ No generaliza | El edge SMC/OB es específico del oro. |
| `atr_sl=1.7`, parcial ≠ 0.3, trail ≠ 2.0 | ❌ | `optimize.py` confirma que lo actual es óptimo. |

**Regla meta:** los knobs de scoring/entrada del VIVO (`min_confluences`, `min_rr`, pesos de
confluencia, ML) **NO son validables con el backtester actual** (ver §6.1). No los "optimices"
con backtest: te mentirá.

---

## 5. Por qué el ML/LSTM no valida (para no repetir el análisis)

- Evaluación rigurosa walk-forward (`eval_ml.py`, `meta_labeling.py --dedup`): con datos limpios
  el ensemble da AUC ≈ 0.38–0.50, proba ALTA 45.8% WR vs BAJA 45.9% = **near-random**.
- Causa raíz = **no-estacionariedad**: el patrón aprendido se invierte al cambiar el régimen del
  oro. Reentrenar sobre la misma historia no lo arregla.
- El CV con folds barajados da AUC 0.86 (FUGA: trades temporalmente vecinos en train+test). El
  `train()` del `LearningEngine` usa `cross_val_score` barajado → su auto-evaluación es OPTIMISTA
  y ENGAÑOSA. **Pendiente menor: cambiarlo a `TimeSeriesSplit`.**
- **Prerrequisito para que algún día funcione:** acumular ~150+ trades LIMPIOS multi-régimen
  (ya con el dedup, cada trade nuevo es dato honesto). Reejecutar `meta_labeling --dedup`;
  activar el meta-gate SOLO si mejora la expectancia OOS >0.05R manteniendo ≥40% de las señales.

---

## 6. LOS 3 PROBLEMAS GRANDES (aquí es donde un modelo superior debe atacar)

### 6.1 ⭐ El backtester NO es el motor en vivo (fallo arquitectónico raíz)
- El **vivo** (`analysis/signal_engine.analyze`) usa 16.5 confluencias (SMC + VP COMEX + delta +
  intermarket + macro + DXY + ML + LSTM + …).
- El **backtester** (`backtest/backtester.py`) usa SOLO bias + OB + vetos de liquidez/vol/horas.
- **Consecuencia:** todo el scoring de confluencias, el ML, el LSTM, `min_confluences` — NADA de
  eso está validado por backtest. El "backtest PF 1.6" NO refleja el comportamiento real.
- **La mejora de mayor impacto de todo el proyecto:** construir un **backtester UNIFICADO** que
  reproduzca `analyze()` barra a barra sobre histórico (con los módulos que dependan de datos
  reproducibles; los que no —ticks, VP COMEX intradía— mockearlos o marcarlos). Si lo logras,
  por primera vez podrás optimizar `min_confluences`, activar/vetar el ML, y medir qué confluencia
  aporta y cuál mete ruido. **Difícil pero es EL desbloqueo.** Empieza por hacer `analyze()`
  inyectable con una fuente de datos histórica en vez de MT5 en vivo.

### 6.2 El sesgo direccional (301 SELL vs 29 BUY en vivo)
- Pese a `counter_trend_veto`, el vivo genera 10× más SELL que BUY, y BUY tiene mejor WR (62% vs 42%).
- Hipótesis a investigar: ¿qué confluencias empujan a SELL? (VP COMEX, intermarket bajista,
  estructura). Audita la distribución de cada `*_score` en señales SELL vs BUY sobre `trades.db`.
- Objetivo: que el bias direccional del vivo sea simétrico y coherente con el HTF real. Esto
  necesita 6.1 para validarse de verdad.

### 6.3 Régimen: mean-reversion (actual) ↔ breakout (ORB) con un clasificador fiable
- El bot es mean-reversion: gana en rango, pierde en tendencia fuerte. El breakout es el espejo.
- Combinarlos con un **clasificador de régimen robusto** (no solo ADX/Hurst, que dan trade-offs)
  que conmute de estrategia sería el mayor salto de rendimiento. El cuello de botella es el
  clasificador. Ideas: HMM, cambio estructural (Bai-Perron), o features de microestructura.

---

## 7. Tareas priorizadas (con criterio de aceptación)

**P0 — Verificar lo de esta sesión en vivo**
- Confirmar que tras reiniciar ya NO llegan señales duplicadas a Telegram (revisar `sent_signals`
  y que en `trades.db` no reaparecen filas casi idénticas por vela).
- Aceptación: 0 duplicados nuevos en 48 h.

**P1 — Backtester unificado (§6.1).** El desbloqueo. Ver arriba.
- Aceptación: poder correr `analyze()` sobre 1 año histórico y reproducir métricas comparables a
  las del vivo; que `min_confluences` y el ML pasen a ser barrido-validables.

**P2 — Auditoría del sesgo direccional (§6.2).**
- Aceptación: informe de qué confluencias causan el exceso de SELL + propuesta validada (con 6.1).

**P3 — Salud de datos y auto-monitorización.**
- Cambiar `LearningEngine.train()` de CV barajado a `TimeSeriesSplit` (auto-eval honesta).
- Añadir un job (semanal) que corra `meta_labeling --dedup` y avise por Telegram cuando el ML
  por fin pase el guardarraíl → activar meta-gate con confianza.
- Revisar el stream **DAYTRADE M15** (WR 28.6% deduplicado, muestra pequeña): auditar o desactivar.

**P4 — Ideas de investigación (cada una con backtest OOS antes de aplicar):**
- Sizing dinámico tipo Kelly fraccionado por confianza/régimen.
- Purged K-Fold CV con embargo (López de Prado) para el meta-labeling cuando haya datos.
- Filtro de contexto suave con `vwap_slope` (evidencia débil, no como señal).

---

## 8. Cómo validar CUALQUIER cambio (checklist)

1. Parar el bot vivo (evita contención MT5).
2. `python main.py --backtest` (año actual) + `backtest/validate_change.py` (año anterior).
3. Aceptar SOLO si: PF↑ y Retorno↑ **y** Max DD no sube, en **ambos** años.
4. Si toca scoring del vivo (ML, confluencias): recuerda §6.1 — el backtester no lo mide;
   necesitas el backtester unificado o validación forward-live.
5. Aplicar con flag; documentar en `ideas/mejoras-sistema-trading.md`; reiniciar el bot.

---

## 9. Fallos concretos que quizá yo pasé por alto (busca aquí)

- **Timezone del scheduler:** `schedule.every().sunday.at(...)` usa hora LOCAL, pero el sistema
  razona en UTC. Revisa que el reporte semanal y cualquier `.at()` disparen a la hora correcta.
- **`macro_feed`/`volume_profile`/`intermarket` en fallo:** ya arreglé el hammering de macro,
  pero VP e intermarket podrían refetch-ear en cada ciclo durante rate-limit (mismo patrón).
  Revisa que respeten cooldown al fallar.
- **`outcome_tracker`:** ¿resuelve bien EXPIRED vs LOSS? ¿El triple-barrier usa el timeframe
  correcto por stream? Un sesgo aquí contamina las stats y el ML.
- **Cálculo de lotes / value_per_lot** para no-oro (por si algún día se reactiva algo).
- **Condiciones de carrera en `trades.db`** (bot escribe + scripts leen). SQLite lo aguanta,
  pero revisa transacciones largas.
- **El dedup nuevo:** su fingerprint usa el timestamp de emisión floored a la vela. Si dos setups
  legítimamente distintos caen en la misma vela H1 + misma dirección, el 2º se suprime. Evalúa si
  eso descarta señales válidas (probablemente no, pero mídelo).

---

## 10. Referencias
- Mapa de código: `CLAUDE.md`
- Backlog vivo: vault Obsidian `ideas/mejoras-sistema-trading.md`
- Nota principal: vault `proyectos/sistema-trading-xauusd.md`
- Memoria de Claude (histórico de decisiones): `project-trading-xauusd` y `feedback-technical-windows`

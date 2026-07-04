# RESUME — Forex + Índices + Scalping M5 (reanudación tras corte de sesión)

> Si la sesión se cortó por límite, en una sesión nueva escribe **"sigue"** y retomo
> desde el primer item sin marcar. Este archivo es la fuente de verdad del progreso.
> Plan completo: `C:\Users\geost\.claude\plans\quiero-modificar-mis-se-ales-memoized-reef.md`

## Objetivo
Añadir señales de **forex (EUR/USD + majors)** e **índices (US30/US100/SP500)** y un
stream de **scalping M5 alert-only**, SIN tocar el oro. Regla dura: **ninguna señal
nueva se activa hasta que un backtest confirme ventaja** (PF>1, WR>breakeven, OOS+).

## Decisiones del usuario (fijas)
- Instrumentos: forex **e** índices.
- Arranque: solo tras backtest con ventaja.
- Scalping: M5, alert-only manual.
- Oro: NO tocar (primary, auto-exec demo, funded — todo intacto).

## Checklist de implementación — ✅ CÓDIGO COMPLETO (2026-06-23)
- [x] 1. `config.yaml`: `symbols.alt`, `symbols.contracts`, sección `scalp:`
- [x] 2. `risk/risk_manager.py`: branch de índices en `calculate_lot_size`
- [x] 3. `analysis/signal_engine.py`: helper `_is_gold`, VP gold-only guardado en `analyze_daytrade`, nuevo `analyze_scalp(symbol)` M5
- [x] 4. `main.py`: `self.alt_symbols`, helper `_emit_manual_signal`, bloque SCALP; auto-exec SOLO sobre `self.symbols`; loop ALT solo alert-only
- [x] 5. `alerts/telegram_bot.py`: rama `mode == "SCALP"`
- [x] 6. `backtest/scalp_backtester.py`: clon M5/M15 por símbolo (con veredicto automático)
- [x] 7. `backtest/discover_symbols.py`: listar símbolos MT5 + value_per_lot
- [x] 8. Verificación: compila OK · `main.py --test` OK · scalp_backtester OK · regresión oro PF 1.56/WR 41.6% (= baseline) OK

## ⏭️ Lo que falta (necesita decidir/datos del usuario + MT5 abierto)
1. `python backtest/discover_symbols.py` → copiar el bloque `contracts` de índices a `config.yaml`
   y la lista de candidatos a `symbols.alt` (NO activar aún).
2. Por CADA símbolo candidato, validar 1 año:
   - `python backtest/daytrade_backtester.py --symbol EURUSD --days 365`
   - `python backtest/scalp_backtester.py   --symbol EURUSD --days 365`
   (índices: añadir `--hours 13-20` y `--spread <real>`)
3. Solo los que el veredicto marque ✅ (PF>1, WR>breakeven, ≥40 trades) + revalidar OOS (año anterior con `--days 730` o ventana previa).
4. Activar ganadores: meterlos en `symbols.alt`, poner `scalp.enabled: true` (daytrade ya on),
   todo `alert_only`. Reiniciar bot (DETENER.bat + INICIAR.bat).

## 🔬 INVESTIGACIÓN + VALIDACIÓN COMPLETA (2026-06-23) — veredicto: NADA robusto

### Resultados (cuenta EUR, coste real: índices/forex = spread-only, comm 0)
**1. Modelo reversión a OB (el del oro) en daytrade M15, 1 año — TODOS pierden:**
| Símbolo | WR | PF | Neto |
|---|---|---|---|
| EURUSD | 32.4% | 0.73 | −23.7R |
| GBPUSD | 27.1% | 0.58 | −38.3R |
| USDJPY | 27.7% | 0.62 | −32.3R |
| US30 | 27.9% | 0.62 | −19.4R |
| USTEC | 38.2% | 0.94 | −3.8R |
| US500 | 34.7% | 0.31 | −58.1R |
→ Es mean-reversion; forex/índices tendencian. No transfiere.

**2. ORB (Opening Range Breakout, momentum — Zarattini/Aziz) + filtro tendencia EMA200,
índices, 200d (máx histórico M5), coste spread-only:**
| Índice | Periodo completo | OOS 1ª mitad | OOS 2ª mitad (reciente) |
|---|---|---|---|
| US30  | PF 1.20 (+7.2R) | PF **1.59** ✅ | PF **0.85** ❌ |
| USTEC | PF 1.04 (+2.0R) | PF **1.37** ✅ | PF **0.80** ❌ |
| US500 | PF 1.17 (+8.3R) | PF **1.74** ✅ | PF **0.83** ❌ |
→ **El edge lo carga la 1ª mitad; en los ~3 meses recientes se invierte en los 3 a la
vez = cambio de RÉGIMEN, no edge robusto. NO se activa.** Re-validar dentro de 1-2 meses.

### Decisión (jefe de proyecto): NADA activado en vivo
- `symbols.alt: []`, `scalp.enabled: false`, ORB sin integrar en el bot. El oro intacto.
- La verdad para la cuenta 2K: no hay rescate vía instrumento nuevo. El único edge validado
  sigue siendo XAUUSD H1 (PF 1.56). Para la 2K = **disciplina de tamaño**, no instrumentos.

### Herramientas dejadas listas (para re-validar cuando cambie el régimen)
- `backtest/orb_backtester.py` — ORB con `--mode breakout|candle --trend EMA --rr --half first|second --comm 0`
- `backtest/daytrade_backtester.py` y `scalp_backtester.py` — ya generalizados (`--symbol`, coste por activo)
- `backtest/discover_symbols.py` — nombres/valor de lote del broker
- Datos del broker: M5 sólo ~200 días de histórico; índices US30/US500/USTEC = 0.875 EUR/punto/lote;
  TZ de los datos MT5 = **servidor UTC+3** (apertura cash US = 16:30 en los datos, 15:30 en invierno).

### Cómo re-validar ORB en el futuro (1 línea)
`python backtest/orb_backtester.py --symbol US30 --days 200 --spread 3 --rr 2 --comm 0 --trend 200 --half second`
Si la 2ª mitad (reciente) da PF>1 de forma estable en US30+USTEC+US500 → entonces integrar `analyze_orb`.

### Forex ORB (London/NY) — también falla
EURUSD PF 0.60 (London) / 0.74 (NY); GBPUSD PF 0.99 (London, breakeven) / 0.75 (NY). Ni in-sample pasa.

## ✅ ENTREGABLE FINAL ÚTIL: calculadora de riesgo fondeado — `risk/funded_calc.py`
La búsqueda de estrategia dio negativo, pero el problema REAL (no quemar la cuenta) sí tiene solución:
dimensionar contra el COLCHÓN (room), no el balance. La calculadora lo hace:
`python risk/funded_calc.py --room 20 --symbol XAUUSD --entry 4195 --sl 4210`

**Integrado en Telegram (2026-06-23):** comando `/lote <room$> <entry> <sl> [símbolo]` →
responde el lote máximo según el colchón. Ej: `/lote 120 4195 4210` (oro) o
`/lote 120 39000 38950 US30`. En `main.py` (`_send_lote`/`_risk_per_lot`) + parseo en telegram_bot.
Verificado: `main.py --test` corre limpio, ruta del oro intacta.

**Verdad demostrada con números (2026-06-23):**
- Situación actual (room ~$20, oro, stop $15): 0.01 lote arriesga $15 = **75% del room → NO VIABLE**.
  La 2K es casi irrecuperable con trades normales de oro.
- 2K fresca + US30 (room $120, stop 50pts): se puede dimensionar **0.27 lotes** = exacto 10% del room.
- → CONFIRMA la intuición original del usuario: forex/índices NO dan más ventaja (refutado), pero SÍ dan
  **granularidad de tamaño** muy superior al oro en cuentas de colchón pequeño. El problema era el TAMAÑO.

### Recomendación final (jefe de proyecto)
1. No activar forex/índices/scalp/ORB en vivo (sin edge validado).
2. Operar solo XAUUSD (único edge, PF 1.56) y SIEMPRE dimensionar con `funded_calc.py` (5-10% del room/trade).
3. Para una cuenta fondeada nueva con colchón pequeño, usar un instrumento de tick fino (US30/EURUSD)
   SOLO por la granularidad de lotaje — no porque tenga edge.

## Pulido pendiente (no bloquea; forex no se activa hasta validar)
- `alerts/telegram_bot.py` bloques MI CUENTA / 2K formatean entry/SL a `.2f` (bien para oro,
  pero forex necesita 5 decimales). Cosmético — arreglar al activar el primer par de forex.

## Notas de arquitectura (no perder)
- Símbolos `alt` NUNCA pasan por `analyze()` (stream H1 auto-ejecutado en demo).
- `calculate_lot_size`: índices usan `symbols.contracts[sym].value_per_lot`.
- Confluencias gold-only (VP COMEX GC=F, DXY, intermarket, news) van tras `_is_gold(sym)`.
- Tras editar código: reiniciar bot (DETENER.bat + INICIAR.bat). MT5 abierto + AutoTrading verde para backtest.

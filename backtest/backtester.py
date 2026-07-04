"""
Backtester — Simulación histórica de la estrategia SMC sobre datos de MT5.

v2 — simula el sistema completo de ejecución:
  - Entrada LIMIT en retroceso al Order Block (con verificación de fill)
  - Breakeven automático al alcanzar +1R
  - Cierre parcial (50%) en TP1 + runner con trailing stop ATR hasta TP2
  - Comisiones por lote round-trip

Métricas calculadas:
  - Win rate, Profit factor, Max drawdown, Sharpe ratio, R medio por trade
  - Retorno total, curva de equity

Ejecutar directamente:
    python backtest/backtester.py             → sistema completo (gestión activa)
    python backtest/backtester.py --classic   → TP fijo en 2R sin gestión
"""

import sys
import os
import csv
import json
import numpy as np
import pandas as pd
import logging
from datetime import datetime, timezone, timedelta

# Ruta absoluta a logs/ — siempre relativa al proyecto, no al CWD
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(_PROJECT_ROOT, "logs")

# Agregar raíz del proyecto al path cuando se ejecuta directamente
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.indicators import add_indicators, get_ema_bias
from analysis.trend_filter import counter_trend_veto, compute_directional_bias
from analysis.market_regime import calculate_adx
from analysis.market_structure import (
    find_order_blocks,
    find_swing_points,
    detect_liquidity_sweep,
    detect_displacement,
    get_significant_levels,
    find_htf_liquidity_pools,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Función principal
# ──────────────────────────────────────────────────────────────

def run_backtest(mt5_connector, symbol: str, timeframe: str,
                 date_from: datetime, date_to: datetime,
                 config: dict,
                 use_filters: bool | None = None,
                 use_reversal: bool | None = None,
                 record_training: bool = False) -> dict:
    """
    Simula la estrategia sobre datos históricos.

    Args:
        mt5_connector : instancia de MT5Connector ya conectada
        symbol        : par a testear (ej: "XAUUSD")
        timeframe     : timeframe de entrada (ej: "H1")
        date_from/to  : rango de fechas del test
        config        : configuración con parámetros de riesgo
        use_filters   : filtros de liquidez (sweep-contra + displacement);
                        None = leer de config["liquidity"]
        use_reversal  : modelo sweep-reversal; None = config["reversal"]["enabled"]

    Returns:
        {"metrics": dict, "trades": list, "equity_curve": list}
    """
    risk_cfg    = config.get("risk", {})
    sess_cfg    = config.get("sessions", {}).get("allowed_hours_utc", {})
    min_rr      = float(risk_cfg.get("min_rr", 2.0))
    atr_mult    = float(risk_cfg.get("atr_sl_multiplier", 1.5))
    risk_pct    = float(risk_cfg.get("risk_per_trade", 0.01))
    capital0    = float(risk_cfg.get("capital", 10000))
    sess_start  = int(sess_cfg.get("start", 0))
    sess_end    = int(sess_cfg.get("end", 23))
    # Horas bloqueadas puntuales (default vacío = sin efecto). Misma fuente que el
    # vivo (risk_manager.is_session_allowed). Valida exclusiones tipo "hora 16 UTC".
    blocked_hours = config.get("sessions", {}).get("blocked_hours_utc", []) or []
    # Puerta de régimen (experimento 2026-06-19): bloquear TODA dirección si la
    # tendencia es FUERTE (ADX H4 >= umbral), porque la reversión a OB también
    # falla a favor de tendencia fuerte (el precio no retrocede al OB, sigue).
    # Default None = sin efecto. Lo honran el vivo (signal_engine) y el backtester.
    regime_block_adx = (config.get("regime", {}) or {}).get("block_strong_trend_adx", None)
    # Comisión round-trip por lote (configurable, default XAUUSD ~$7)
    comm_cfg    = config.get("backtest", {})
    comm_per_lot = float(comm_cfg.get("commission_per_lot", 7.0))
    # Slippage round-trip simulado en unidades de ATR (default 0 = sin slippage).
    # El SL del backtester ≈ atr_sl_multiplier×ATR, así que en R: slip_r = slippage_atr/atr_mult.
    # Se descuenta a CADA trade (entrada+salida pagan deslizamiento). Realismo de costes.
    slippage_atr = float(comm_cfg.get("slippage_atr", 0.0))
    slip_r       = (slippage_atr / atr_mult) if atr_mult > 0 else 0.0

    # Gestión activa (igual que el sistema en vivo)
    mgmt_cfg     = config.get("trading", {}).get("management", {}) or {}
    managed      = bool(mgmt_cfg.get("enabled", True))
    partial_pct  = float(mgmt_cfg.get("partial_close_pct", 0.5))
    trail_mult   = float(mgmt_cfg.get("trail_atr_mult", 2.0))
    fill_window  = int(config.get("trading", {}).get("order_expiry_hours", 8))

    # Filtros de liquidez + modelo reversal (mismos parámetros que en vivo)
    liq_cfg = config.get("liquidity", {}) or {}
    rev_cfg = config.get("reversal", {}) or {}
    if use_filters is None:
        use_filters = bool(liq_cfg.get("sweep_against_veto", False)
                           or liq_cfg.get("displacement_filter", False))
    if use_reversal is None:
        use_reversal = bool(rev_cfg.get("enabled", False))
    sweep_veto_on = use_filters and bool(liq_cfg.get("sweep_against_veto", True))
    disp_veto_on  = use_filters and bool(liq_cfg.get("displacement_filter", True))
    htf_veto_on   = use_filters and bool(liq_cfg.get("htf_liquidity_veto", False))
    htf_max_atr   = float(liq_cfg.get("htf_liquidity_atr", 1.5))
    htf_lookback  = int(liq_cfg.get("htf_swing_lookback", 3))
    liq_lookback  = int(liq_cfg.get("sweep_against_lookback", 6))
    disp_candles  = int(liq_cfg.get("displacement_candles", 3))
    disp_body     = float(liq_cfg.get("displacement_body_atr", 0.8))
    disp_net      = float(liq_cfg.get("displacement_net_atr", 1.5))

    # Puerta direccional HTF (fix sesgo 2026-06-17) — mismo criterio que el vivo.
    # Independiente de use_filters para poder medir su impacto aislado.
    tf_cfg        = config.get("trend_filter", {}) or {}
    trend_veto_on = bool(tf_cfg.get("counter_trend_veto", True))

    # Filtro de volatilidad por PERCENTIL de ATR (research + autopsia atr_pct r=-0.37).
    # La reversión a OB falla cuando la vol cambia a régimen de tendencia. Se mide
    # el percentil del ATR actual vs su ventana reciente y se salta la barra si está
    # en el extremo alto.
    vol_cfg       = config.get("volatility", {}) or {}
    vol_filter_on = bool(vol_cfg.get("vol_filter", False))
    vol_pct_max   = float(vol_cfg.get("vol_pct_max", 0.90))
    vol_window    = int(vol_cfg.get("vol_window", 150))
    rev_lookback  = int(rev_cfg.get("lookback_bars", 8))
    rev_sl_buffer = float(rev_cfg.get("sl_buffer_atr_m15", 0.5))

    vetoed_by_sweep        = 0
    vetoed_by_displacement = 0
    vetoed_by_htf          = 0
    vetoed_by_trend        = 0
    vetoed_by_regime       = 0

    _print_header(symbol, timeframe, date_from, date_to)
    print(f"  Sesión permitida: {sess_start:02d}:00 – {sess_end:02d}:59 UTC")
    print(f"  Comisión simulada: ${comm_per_lot:.1f}/lote round-trip")
    if slippage_atr > 0:
        print(f"  Slippage simulado: {slippage_atr:.2f}×ATR round-trip ({slip_r:.3f}R/trade)")
    print(f"  Entrada: LIMIT en retroceso al OB (fill window {fill_window}h)")
    print(f"  Filtros liquidez: {'ON (sweep-contra + displacement)' if use_filters else 'OFF (baseline)'}")
    print(f"  Modelo reversal:  {'ON' if use_reversal else 'OFF'}")
    if managed:
        print(f"  Gestión: BE@1R + parcial {partial_pct:.0%}@TP1 + trailing {trail_mult}xATR → TP2")
    else:
        print(f"  Gestión: clásica — TP fijo en {min_rr}R")

    # ── Descargar datos ───────────────────────────────────────
    print("  Descargando datos históricos...")
    df_h1   = mt5_connector.get_rates_range(symbol, timeframe, date_from, date_to)
    df_h4   = mt5_connector.get_rates_range(symbol, "H4",     date_from, date_to)
    # D1 para el veto de liquidez HTF (pools intactos de marco superior)
    df_d1   = pd.DataFrame()
    if htf_veto_on:
        try:
            df_d1 = mt5_connector.get_rates_range(symbol, "D1", date_from, date_to)
        except Exception:
            df_d1 = pd.DataFrame()

    if df_h1.empty:
        print("  ERROR: Sin datos históricos. ¿MT5 está conectado?")
        return {}

    total_bars = len(df_h1)
    print(f"  Datos cargados: {total_bars:,} velas {timeframe}  |  {len(df_h4):,} velas H4")

    # ── Simulación vela a vela ────────────────────────────────
    trades   = []
    equity   = [capital0]
    min_win  = 250  # calentamiento mínimo antes de analizar
    last_signal_bar = -1  # cooldown entre señales

    for i in range(min_win, total_bars - 50):
        if i % 1000 == 0:
            pct = (i - min_win) / max(1, total_bars - min_win - 50) * 100
            print(f"  [{pct:5.1f}%]  Trades: {len(trades)}  |  Equity: ${equity[-1]:,.0f}")

        # Cooldown de 6 barras (6h en H1) entre señales
        if i - last_signal_bar < 6:
            continue

        # Ventana de datos hasta la barra actual
        window_h1 = add_indicators(df_h1.iloc[:i + 1].copy())
        if window_h1.empty or len(window_h1) < 50:
            continue

        # Filtro de sesión: solo operar en horas configuradas
        current_time = df_h1["time"].iloc[i]
        bar_hour = current_time.hour if hasattr(current_time, "hour") else pd.Timestamp(current_time).hour
        if not (sess_start <= bar_hour < sess_end) or bar_hour in blocked_hours:
            continue

        # Filtro RSI: no comprar sobrecompra, no vender sobreventa
        rsi_val = window_h1["rsi"].iloc[-1] if "rsi" in window_h1.columns else 50.0
        if pd.isna(rsi_val):
            rsi_val = 50.0

        # ATR y precio actuales (los necesitan todos los modelos)
        current_price = float(window_h1["close"].iloc[-1])
        atr = window_h1["atr"].iloc[-1]
        if pd.isna(atr) or float(atr) == 0:
            continue
        atr = float(atr)

        # Filtro de volatilidad por percentil: en vol extrema (regime change a
        # tendencia) la reversión a OB pierde → no operar esa barra.
        if vol_filter_on:
            atr_hist = window_h1["atr"].tail(vol_window)
            if len(atr_hist) >= 30 and float((atr_hist <= atr).mean()) >= vol_pct_max:
                continue

        # Swings recientes (sweep-contra y reversal) — tail por rendimiento
        if use_filters or use_reversal:
            tail_sw = window_h1.tail(120).reset_index(drop=True)
            sw_highs, sw_lows = find_swing_points(tail_sw)
        else:
            tail_sw, sw_highs, sw_lows = window_h1, [], []

        # Bias H4 hasta la barra actual
        h4_window    = df_h4[df_h4["time"] <= current_time].tail(200).copy()
        if len(h4_window) < 30:
            continue
        h4_ind = add_indicators(h4_window)
        # Bias unificado con el vivo (EMA + estructura, mismo criterio)
        bias, bt_direction = compute_directional_bias(h4_ind, lookback=5)

        # ── Modelo 1: OB retroceso (estrategia base validada) ─────
        setup = None
        if bias != "NEUTRAL":
            direction = bt_direction

            # RSI: rechazar setups contra momentum extremo
            rsi_extreme = (direction == "BUY" and float(rsi_val) > 65) or \
                          (direction == "SELL" and float(rsi_val) < 35)

            # Puerta direccional HTF: no operar contra tendencia fuerte (fix sesgo)
            vetoed = False
            _adx_h4 = None
            if trend_veto_on:
                _adx_h4 = calculate_adx(h4_ind) if tf_cfg.get("adx_veto", False) else None
                ct_blocked, _ = counter_trend_veto(direction, h4_ind, None, tf_cfg,
                                                   adx=_adx_h4)
                if ct_blocked:
                    vetoed_by_trend += 1
                    vetoed = True

            # Puerta de régimen: si la tendencia es fuerte (ADX H4 >= umbral),
            # saltar en CUALQUIER dirección (la reversión a OB falla en tendencia).
            if not vetoed and regime_block_adx is not None:
                if _adx_h4 is None:
                    _adx_h4 = calculate_adx(h4_ind)
                if _adx_h4 is not None and _adx_h4 >= float(regime_block_adx):
                    vetoed_by_regime += 1
                    vetoed = True

            if not rsi_extreme and not vetoed:
                # Filtros de liquidez (mismos parámetros que signal_engine).
                # El veto sweep-contra SOLO actúa sobre niveles significativos
                # (equal highs/lows, PDH/PDL, sesión) — barrer un swing menor
                # no es un liquidity grab institucional
                if sweep_veto_on:
                    opposite = "SELL" if direction == "BUY" else "BUY"
                    sig_levels = get_significant_levels(
                        window_h1.tail(150).reset_index(drop=True))
                    lvls = sig_levels["lows"] if opposite == "BUY" else sig_levels["highs"]
                    if lvls:
                        swept_against = detect_liquidity_sweep(
                            tail_sw, sw_highs, sw_lows, opposite,
                            lookback=liq_lookback, levels=lvls)
                        if swept_against["swept"]:
                            vetoed_by_sweep += 1
                            vetoed = True
                if not vetoed and disp_veto_on:
                    disp = detect_displacement(
                        window_h1, atr, n_candles=disp_candles,
                        body_atr=disp_body, net_atr=disp_net, exclude_last=False)
                    if (disp["direction"] == "BULLISH" and direction == "SELL") or \
                       (disp["direction"] == "BEARISH" and direction == "BUY"):
                        vetoed_by_displacement += 1
                        vetoed = True
                # Veto liquidez HTF intacta (mismo criterio que signal_engine):
                # no operar de frente contra pool H4/D1 sin barrer
                if not vetoed and htf_veto_on:
                    d1_window = df_d1[df_d1["time"] <= current_time].tail(90) \
                        if not df_d1.empty else pd.DataFrame()
                    pools = find_htf_liquidity_pools(
                        [(h4_ind, "H4"), (d1_window, "D1")],
                        current_price, atr,
                        max_dist_atr=htf_max_atr, swing_lookback=htf_lookback)
                    in_path = pools["above"] if direction == "SELL" else pools["below"]
                    if in_path:
                        swept_own = detect_liquidity_sweep(
                            tail_sw, sw_highs, sw_lows, direction,
                            lookback=liq_lookback,
                            levels=[p["price"] for p in in_path])
                        if not swept_own["swept"]:
                            vetoed_by_htf += 1
                            vetoed = True

            if not rsi_extreme and not vetoed:
                obs = find_order_blocks(window_h1, n_recent=30)
                target_type = "BULLISH" if direction == "BUY" else "BEARISH"
                ob = _find_ob(obs, current_price, direction, atr, target_type)
                if ob is not None:
                    # Niveles — entrada LIMIT en retroceso al OB (como en vivo)
                    inside_ob = ob["bottom"] <= current_price <= ob["top"]
                    if direction == "BUY":
                        entry = current_price if inside_ob else ob["top"]
                        sl    = ob["bottom"] - (atr * atr_mult)
                    else:
                        entry = current_price if inside_ob else ob["bottom"]
                        sl    = ob["top"] + (atr * atr_mult)
                    entry_type = "MARKET" if inside_ob else "RETRACEMENT"
                    risk = abs(entry - sl)
                    if risk > 0:
                        tp1 = entry + risk * min_rr if direction == "BUY" else entry - risk * min_rr
                        tp2 = entry + risk * 3.0    if direction == "BUY" else entry - risk * 3.0
                        setup = {"direction": direction, "entry": entry, "sl": sl,
                                 "tp1": tp1, "tp2": tp2, "entry_type": entry_type,
                                 "model": "OB"}

        # ── Modelo 2: sweep-reversal (sin requisito de bias H4) ───
        # Aproximación H1: CHoCH M15 sustituido por displacement de reclaim
        if setup is None and use_reversal:
            setup = _check_reversal_setup(
                window_h1, tail_sw, sw_highs, sw_lows, atr,
                lookback=rev_lookback, sl_buffer_atr=rev_sl_buffer, min_rr=min_rr)

        if setup is None:
            continue

        direction  = setup["direction"]
        entry      = setup["entry"]
        sl         = setup["sl"]
        tp1        = setup["tp1"]
        tp2        = setup["tp2"]
        entry_type = setup["entry_type"]
        rr         = min_rr

        # Simular: fill de la LIMIT + gestión activa (BE, parcial, trailing)
        outcome = _simulate_trade_managed(
            df_h1, i, direction, entry, sl, tp1, tp2, atr,
            managed=managed, partial_pct=partial_pct, trail_mult=trail_mult,
            fill_window=fill_window, max_bars=80,
        )
        if outcome["result"] == "OPEN":
            continue  # Trade abierto al final del período → ignorar
        if outcome["result"] == "NO_FILL":
            last_signal_bar = i  # cuenta para cooldown pero no como trade
            continue

        # Calcular P&L con comisión descontada
        # value_per_pt = pip_value / pip_size (dólares por unidad de precio por lote)
        # XAUUSD: 1pt=$100/lot | EURUSD/GBPUSD: 1pt=$100k/lot | USDJPY: 1pt~$900/lot
        _vpt_map = {"XAUUSD": 100.0, "EURUSD": 100000.0,
                    "GBPUSD": 100000.0, "USDJPY": 900.0}
        value_per_pt  = _vpt_map.get(symbol, 100000.0)
        sl_dist       = abs(entry - sl)
        lot_size_est  = (equity[-1] * risk_pct) / (sl_dist * value_per_pt) if sl_dist > 0 else 0.01
        comm_total    = lot_size_est * comm_per_lot
        comm_pct      = comm_total / equity[-1]  # comisión como % del capital actual

        pnl_rr  = outcome["pnl_r"] - slip_r       # descontar slippage round-trip (en R)
        pnl_pct = pnl_rr * risk_pct - comm_pct    # descontar comisión siempre
        new_eq  = equity[-1] * (1 + pnl_pct)
        equity.append(new_eq)
        last_signal_bar = i

        trades.append({
            "entry_time":     current_time.isoformat() if hasattr(current_time, "isoformat") else str(current_time),
            "exit_time":      outcome["exit_time"],
            "symbol":         symbol,
            "direction":      direction,
            "hour_utc":       bar_hour,
            "entry":          round(entry, 5),
            "entry_type":     entry_type,
            "sl":             round(sl, 5),
            "tp1":            round(tp1, 5),
            "tp2":            round(tp2, 5),
            "rr":             round(rr, 2),
            "rsi":            round(float(rsi_val), 1),
            "result":         outcome["result"],
            "pnl_rr":         round(pnl_rr, 2),
            "pnl_pct":        round(pnl_pct * 100, 3),
            "equity":         round(new_eq, 2),
            "bars_to_close":  outcome["bars"],
            "bias":           bias,
            "model":          setup["model"],
        })

    # ── Muestras de entrenamiento (pipeline backtest → ML/NN) ──
    # Cada trade WIN/LOSS se guarda como features+outcome (source=backtest) para
    # que LearningEngine/NeuralEngine aprendan de miles de trades, no solo de los
    # ~245 en vivo. El backtester es crudo → features limitadas (las que tiene);
    # el resto va a default neutro en _extract_features.
    if record_training:
        try:
            from ml.training_store import record_samples, clear_source
            samples = []
            for t in trades:
                if t["result"] not in ("WIN", "LOSS"):
                    continue
                rsi = t.get("rsi", 50)
                rsi_state = "OVERSOLD" if rsi < 35 else ("OVERBOUGHT" if rsi > 65 else "NEUTRAL")
                samples.append({
                    "timestamp":  t["entry_time"],
                    "direction":  t["direction"],
                    "bias_h4":    t.get("bias", "NEUTRAL"),
                    "ob_type":    "BULLISH" if t["direction"] == "BUY" else "BEARISH",
                    "rsi_state":  rsi_state,
                    "rr":         t.get("rr", 2.0),
                    "entry_type": t.get("entry_type", ""),
                    "outcome":    t["result"],
                })
            db_path = config.get("logging", {}).get("db_path", "logs/trades.db")
            if not os.path.isabs(db_path):
                db_path = os.path.join(_PROJECT_ROOT, db_path)
            clear_source(db_path, "backtest")
            record_samples(db_path, samples, source="backtest")
        except Exception as e:
            logger.warning(f"No se pudieron guardar muestras de entrenamiento: {e}")

    # ── Métricas ──────────────────────────────────────────────
    metrics = _calculate_metrics(trades, equity)
    metrics["vetoed_by_sweep"]        = vetoed_by_sweep
    metrics["vetoed_by_displacement"] = vetoed_by_displacement
    metrics["vetoed_by_htf"]          = vetoed_by_htf
    metrics["vetoed_by_trend"]        = vetoed_by_trend
    metrics["vetoed_by_regime"]       = vetoed_by_regime
    metrics["filters_on"]             = bool(use_filters)
    metrics["reversal_on"]            = bool(use_reversal)

    # Métricas separadas del modelo reversal
    rev_trades = [t for t in trades if t.get("model") == "SWEEP_REVERSAL"
                  and t["result"] in ("WIN", "LOSS", "SCRATCH")]
    if rev_trades:
        rev_wins  = [t for t in rev_trades if t["result"] == "WIN"]
        rev_loss  = [t for t in rev_trades if t["result"] == "LOSS"]
        rev_gp    = sum(t["pnl_rr"] for t in rev_trades if t["pnl_rr"] > 0)
        rev_gl    = abs(sum(t["pnl_rr"] for t in rev_trades if t["pnl_rr"] < 0))
        decided   = len(rev_wins) + len(rev_loss)
        metrics["reversal_trades"] = len(rev_trades)
        metrics["reversal_wr"]     = round(len(rev_wins) / decided, 4) if decided else 0
        metrics["reversal_pf"]     = round(rev_gp / rev_gl, 2) if rev_gl > 0 else float("inf")
    else:
        metrics["reversal_trades"] = 0

    _print_results(metrics)
    _save_results(trades, symbol, timeframe)
    _save_metrics_json(metrics, equity, symbol, timeframe, date_from, date_to,
                       variant="filters" if use_filters else None)

    return {"metrics": metrics, "trades": trades, "equity_curve": equity}


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _check_reversal_setup(window_h1: pd.DataFrame, tail_sw: pd.DataFrame,
                          sw_highs: list, sw_lows: list, atr: float,
                          lookback: int = 8, sl_buffer_atr: float = 0.5,
                          min_rr: float = 2.0) -> dict | None:
    """
    Modelo sweep-reversal simplificado para backtest (solo H1):
      1. Una vela reciente barrió un nivel significativo (equal low/high,
         PDH/PDL, high/low de sesión) y CERRÓ de vuelta (reclaim).
      2. Displacement H1 en la dirección de la reversión (sustituye al
         CHoCH M15 del modelo en vivo — aproximación documentada).
      3. Entry LIMIT en el 50% del leg de reclaim; SL bajo/sobre la mecha.

    Returns: setup dict o None.
    """
    # Solo las últimas ~150 velas: PDH/PDL y equal highs/lows recientes
    # (el window completo crece a miles de barras → O(n²) prohibitivo)
    levels = get_significant_levels(window_h1.tail(150).reset_index(drop=True))

    for direction, level_list in (("BUY", levels["lows"]), ("SELL", levels["highs"])):
        if not level_list:
            continue  # sin niveles significativos → sin setup de reversión
        sweep = detect_liquidity_sweep(
            tail_sw, sw_highs, sw_lows, direction,
            lookback=lookback, levels=level_list,
        )
        if not sweep["swept"] or sweep.get("wick_extreme") is None:
            continue
        # Frescura: el barrido debe ser reciente (la reversión se enfría)
        if sweep.get("bars_ago") is not None and sweep["bars_ago"] > 4:
            continue

        # Confirmación: displacement FUERTE en la dirección de la reversión
        disp = detect_displacement(window_h1, atr, n_candles=3,
                                   body_atr=0.8, net_atr=1.2, exclude_last=False)
        expected = "BULLISH" if direction == "BUY" else "BEARISH"
        if disp["direction"] != expected:
            continue

        close = float(window_h1["close"].iloc[-1])
        wick  = float(sweep["wick_extreme"])

        # No perseguir: si el precio ya se alejó > 1.5 ATR del nivel barrido,
        # el tren se fue — el R:R real sería mucho peor que el teórico
        if abs(close - float(sweep["level"])) > atr * 1.5:
            continue

        # Entry: 50% de retroceso del leg de reclaim (wick → close)
        entry = (close + wick) / 2.0
        if direction == "BUY":
            sl = wick - atr * sl_buffer_atr
            risk = entry - sl
            if risk <= 0:
                continue
            tp1 = entry + risk * min_rr
            tp2 = entry + risk * 3.0
        else:
            sl = wick + atr * sl_buffer_atr
            risk = sl - entry
            if risk <= 0:
                continue
            tp1 = entry - risk * min_rr
            tp2 = entry - risk * 3.0

        return {"direction": direction, "entry": entry, "sl": sl,
                "tp1": tp1, "tp2": tp2, "entry_type": "REVERSAL",
                "model": "SWEEP_REVERSAL"}

    return None


def _find_ob(obs, price, direction, atr, target_type):
    """Encuentra el OB válido más cercano, alineado con la dirección."""
    for ob in obs:
        if not ob["valid"] or ob["type"] != target_type:
            continue
        if direction == "BUY":
            if 0 <= (price - ob["top"]) <= atr * 2:
                return ob
            if ob["bottom"] <= price <= ob["top"]:
                return ob
        elif direction == "SELL":
            if 0 <= (ob["bottom"] - price) <= atr * 2:
                return ob
            if ob["bottom"] <= price <= ob["top"]:
                return ob
    return None


def _simulate_trade_managed(df: pd.DataFrame, entry_bar: int, direction: str,
                            entry: float, sl: float, tp1: float, tp2: float,
                            atr: float, managed: bool = True,
                            partial_pct: float = 0.5, trail_mult: float = 2.0,
                            fill_window: int = 8, max_bars: int = 80) -> dict:
    """
    Simula el ciclo completo de un trade como en vivo:
      1. Fill de la orden LIMIT (el precio debe TOCAR el entry; si no → NO_FILL)
      2. Breakeven al alcanzar +1R (solo modo managed)
      3. Cierre parcial en TP1 + runner con trailing ATR hasta TP2 (managed)
      o TP fijo en TP1 (modo clásico)

    Returns: {"result", "pnl_r", "exit_time", "bars"}
    """
    is_buy = direction == "BUY"
    risk   = abs(entry - sl)
    if risk == 0:
        return {"result": "NO_FILL", "pnl_r": 0.0, "exit_time": None, "bars": 0}

    def _exit_t(candle):
        t = candle["time"]
        return t.isoformat() if hasattr(t, "isoformat") else str(t)

    def _r(price):
        return (price - entry) / risk if is_buy else (entry - price) / risk

    # ── Fase 1: fill de la LIMIT ──────────────────────────────────
    fill_j = None
    for j in range(1, fill_window + 1):
        if entry_bar + j >= len(df):
            return {"result": "OPEN", "pnl_r": 0.0, "exit_time": None, "bars": j}
        candle = df.iloc[entry_bar + j]
        low, high = float(candle["low"]), float(candle["high"])

        touched = (is_buy and low <= entry) or (not is_buy and high >= entry)
        if touched:
            fill_j = j
            # Vela del fill: lo conservador es asumir SL primero
            if (is_buy and low <= sl) or (not is_buy and high >= sl):
                return {"result": "LOSS", "pnl_r": -1.0,
                        "exit_time": _exit_t(candle), "bars": j}
            break

    if fill_j is None:
        return {"result": "NO_FILL", "pnl_r": 0.0, "exit_time": None, "bars": fill_window}

    # ── Fase 2: gestión del trade ─────────────────────────────────
    be_active    = False
    partial_done = False
    banked_r     = 0.0
    runner_frac  = 1.0 - partial_pct
    best_price   = entry  # extremo favorable alcanzado (para trailing)
    one_r_price  = entry + risk if is_buy else entry - risk

    for j in range(fill_j, fill_j + max_bars + 1):
        if entry_bar + j >= len(df):
            return {"result": "OPEN", "pnl_r": 0.0, "exit_time": None, "bars": j}

        candle = df.iloc[entry_bar + j]
        low, high = float(candle["low"]), float(candle["high"])
        exit_t = _exit_t(candle)

        # Stop efectivo de esta vela
        if partial_done:
            trail = best_price - atr * trail_mult if is_buy else best_price + atr * trail_mult
            eff_sl = max(trail, entry) if is_buy else min(trail, entry)
        elif be_active and managed:
            eff_sl = entry
        else:
            eff_sl = sl

        # 1) Check de stop (conservador: stop antes que target)
        stop_hit = (is_buy and low <= eff_sl) or (not is_buy and high >= eff_sl)
        if stop_hit:
            if partial_done:
                pnl = banked_r + runner_frac * _r(eff_sl)
                result = "WIN" if pnl > 0.05 else ("SCRATCH" if pnl >= -0.05 else "LOSS")
            elif be_active and managed:
                pnl, result = 0.0, "SCRATCH"
            else:
                pnl, result = -1.0, "LOSS"
            return {"result": result, "pnl_r": pnl, "exit_time": exit_t, "bars": j}

        # 2) Breakeven al alcanzar +1R
        if managed and not be_active:
            if (is_buy and high >= one_r_price) or (not is_buy and low <= one_r_price):
                be_active = True

        # 3) TP1
        tp1_hit = (is_buy and high >= tp1) or (not is_buy and low <= tp1)
        if tp1_hit and not partial_done:
            if not managed:
                return {"result": "WIN", "pnl_r": _r(tp1),
                        "exit_time": exit_t, "bars": j}
            banked_r     = partial_pct * _r(tp1)
            partial_done = True
            best_price   = high if is_buy else low

        # 4) TP2 — el runner completo sale
        if partial_done:
            tp2_hit = (is_buy and high >= tp2) or (not is_buy and low <= tp2)
            if tp2_hit:
                pnl = banked_r + runner_frac * _r(tp2)
                return {"result": "WIN", "pnl_r": pnl, "exit_time": exit_t, "bars": j}
            # Actualizar extremo favorable para el trailing
            best_price = max(best_price, high) if is_buy else min(best_price, low)

    # Sin resolución en max_bars → cerrar a precio de la última vela revisada
    last   = df.iloc[min(entry_bar + fill_j + max_bars, len(df) - 1)]
    close  = float(last["close"])
    if partial_done:
        pnl = banked_r + runner_frac * _r(close)
    else:
        pnl = _r(close)
    result = "WIN" if pnl > 0.05 else ("SCRATCH" if pnl >= -0.05 else "LOSS")
    return {"result": result, "pnl_r": pnl, "exit_time": _exit_t(last), "bars": max_bars}


def _calculate_metrics(trades: list, equity: list) -> dict:
    """Calcula métricas de rendimiento a partir de la lista de trades."""
    closed = [t for t in trades if t["result"] in ("WIN", "LOSS", "SCRATCH")]
    if not closed:
        return {"error": "Sin trades cerrados en el período"}

    wins      = [t for t in closed if t["result"] == "WIN"]
    losses    = [t for t in closed if t["result"] == "LOSS"]
    scratches = [t for t in closed if t["result"] == "SCRATCH"]

    decided       = len(wins) + len(losses)
    win_rate      = len(wins) / decided if decided > 0 else 0
    gross_profit  = sum(t["pnl_rr"] for t in closed if t["pnl_rr"] > 0)
    gross_loss    = abs(sum(t["pnl_rr"] for t in closed if t["pnl_rr"] < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    avg_r         = float(np.mean([t["pnl_rr"] for t in closed]))

    # Max drawdown sobre la curva de equity
    eq  = np.array(equity)
    pk  = np.maximum.accumulate(eq)
    dd  = (pk - eq) / pk
    mdd = float(dd.max()) if len(dd) > 0 else 0.0

    # Sharpe ratio (retornos por trade, anualizado aproximado)
    pnl_arr = np.array([t["pnl_pct"] for t in closed])
    sharpe  = (np.mean(pnl_arr) / np.std(pnl_arr) * np.sqrt(252)) if np.std(pnl_arr) > 0 else 0.0

    avg_win_rr  = np.mean([t["pnl_rr"] for t in wins])   if wins   else 0.0
    avg_loss_rr = abs(np.mean([t["pnl_rr"] for t in losses])) if losses else 0.0
    avg_bars    = np.mean([t["bars_to_close"] for t in closed])

    return {
        "total_trades":     len(closed),
        "wins":             len(wins),
        "losses":           len(losses),
        "scratches":        len(scratches),
        "win_rate":         round(win_rate, 4),
        "profit_factor":    round(profit_factor, 2),
        "avg_r_per_trade":  round(avg_r, 3),
        "max_drawdown":     round(mdd, 4),
        "sharpe_ratio":     round(sharpe, 2),
        "avg_winner_rr":    round(avg_win_rr, 2),
        "avg_loser_rr":     round(avg_loss_rr, 2),
        "avg_bars":         round(avg_bars, 1),
        "initial_equity":   round(equity[0], 2)  if equity else 0,
        "final_equity":     round(equity[-1], 2) if equity else 0,
        "total_return_pct": round((equity[-1] / equity[0] - 1) * 100, 2) if len(equity) > 1 else 0,
    }


def _print_header(symbol, timeframe, date_from, date_to):
    print(f"\n{'='*55}")
    print(f"  BACKTEST: {symbol} | {timeframe}")
    print(f"  Período:  {date_from.strftime('%Y-%m-%d')} → {date_to.strftime('%Y-%m-%d')}")
    print(f"{'='*55}")


def _print_results(m: dict):
    if "error" in m:
        print(f"\n  {m['error']}")
        return
    print(f"\n{'─'*55}")
    print(f"  RESULTADOS")
    print(f"{'─'*55}")
    print(f"  Total trades:      {m['total_trades']}")
    print(f"  Ganadores:         {m['wins']}  ({m['win_rate']:.1%})")
    print(f"  Perdedores:        {m['losses']}")
    print(f"  Scratch (BE):      {m.get('scratches', 0)}")
    print(f"{'─'*55}")
    print(f"  Win Rate:          {m['win_rate']:.1%}")
    print(f"  Profit Factor:     {m['profit_factor']}")
    print(f"  R medio/trade:     {m.get('avg_r_per_trade', 0):+.3f}R")
    print(f"  Max Drawdown:      {m['max_drawdown']:.1%}")
    print(f"  Sharpe Ratio:      {m['sharpe_ratio']}")
    print(f"{'─'*55}")
    print(f"  R:R medio (WIN):   {m['avg_winner_rr']}")
    print(f"  R:R medio (LOSS):  {m['avg_loser_rr']}")
    print(f"  Barras promedio:   {m['avg_bars']}")
    print(f"{'─'*55}")
    print(f"  Retorno total:     {m['total_return_pct']:+.2f}%")
    print(f"  Capital final:     ${m['final_equity']:,.2f}  (inicio: ${m['initial_equity']:,.2f})")
    if m.get("filters_on"):
        print(f"{'─'*55}")
        print(f"  Vetos tendencia HTF:  {m.get('vetoed_by_trend', 0)}")
        print(f"  Vetos sweep-contra:   {m.get('vetoed_by_sweep', 0)}")
        print(f"  Vetos displacement:   {m.get('vetoed_by_displacement', 0)}")
        print(f"  Vetos liquidez HTF:   {m.get('vetoed_by_htf', 0)}")
    if m.get("reversal_trades", 0) > 0:
        print(f"{'─'*55}")
        print(f"  REVERSAL trades:      {m['reversal_trades']}")
        print(f"  REVERSAL win rate:    {m.get('reversal_wr', 0):.1%}")
        print(f"  REVERSAL PF:          {m.get('reversal_pf', 0)}")
    print(f"{'='*55}\n")


def _save_results(trades: list, symbol: str, timeframe: str):
    if not trades:
        return
    os.makedirs(LOGS_DIR, exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(LOGS_DIR, f"backtest_{symbol}_{timeframe}_{ts}.csv")
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
        writer.writeheader()
        writer.writerows(trades)
    print(f"  Resultados guardados: {filename}")


def _save_metrics_json(metrics: dict, equity: list,
                       symbol: str, timeframe: str,
                       date_from: datetime, date_to: datetime,
                       variant: str | None = None):
    """
    Guarda métricas y curva de equity como JSON para el dashboard.
    variant="filters" → guarda ADEMÁS una copia en backtest_metrics_filters.json
    para poder comparar baseline vs filtros sin pisar el archivo principal.
    """
    os.makedirs(LOGS_DIR, exist_ok=True)

    meta = {
        "symbol":    symbol,
        "timeframe": timeframe,
        "date_from": date_from.strftime("%Y-%m-%d"),
        "date_to":   date_to.strftime("%Y-%m-%d"),
        "generated": datetime.now().isoformat(),
        "variant":   variant or "baseline",
    }

    payload = {"meta": meta, "metrics": metrics}
    metrics_file = os.path.join(LOGS_DIR, "backtest_metrics.json")
    with open(metrics_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    if variant:
        variant_file = os.path.join(LOGS_DIR, f"backtest_metrics_{variant}.json")
        with open(variant_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"  JSON variante guardado: {variant_file}")

    equity_file = os.path.join(LOGS_DIR, "backtest_equity.json")
    with open(equity_file, "w", encoding="utf-8") as f:
        json.dump(equity, f)

    print(f"  JSON guardado: {metrics_file}")


# ──────────────────────────────────────────────────────────────
# Ejecución directa
# ──────────────────────────────────────────────────────────────

def parse_cli_flags(argv: list) -> dict:
    """
    Flags compartidas entre `python backtest/backtester.py` y
    `python main.py --backtest`:
      --no-filters         desactiva sweep-contra + displacement (baseline)
      --no-reversal        desactiva el modelo sweep-reversal
      --baseline           equivale a --no-filters --no-reversal
      --from YYYY-MM-DD    fecha inicial (default: hace 365 días)
      --to YYYY-MM-DD      fecha final   (default: ahora)
    """
    baseline = "--baseline" in argv
    flags = {
        "use_filters":  False if (baseline or "--no-filters" in argv) else None,
        "use_reversal": False if (baseline or "--no-reversal" in argv) else None,
        "date_from":    None,
        "date_to":      None,
    }
    for key, name in (("date_from", "--from"), ("date_to", "--to")):
        if name in argv:
            try:
                idx = argv.index(name)
                flags[key] = datetime.strptime(argv[idx + 1], "%Y-%m-%d").replace(
                    tzinfo=timezone.utc)
            except (IndexError, ValueError):
                print(f"  ADVERTENCIA: {name} requiere fecha YYYY-MM-DD — ignorado")
    return flags


if __name__ == "__main__":
    import yaml
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if "--classic" in sys.argv:
        config.setdefault("trading", {}).setdefault("management", {})["enabled"] = False

    flags = parse_cli_flags(sys.argv)

    from data.mt5_connector import MT5Connector
    conn = MT5Connector(config)
    if not conn.connect():
        print("ERROR: No se pudo conectar a MT5. ¿Está abierto?")
        sys.exit(1)

    date_to   = flags["date_to"]   or datetime.now(timezone.utc)
    date_from = flags["date_from"] or (date_to - timedelta(days=365))

    run_backtest(conn, "XAUUSD", "H1", date_from, date_to, config,
                 use_filters=flags["use_filters"],
                 use_reversal=flags["use_reversal"])
    conn.disconnect()

"""
Trend Filter — Puerta direccional HTF compartida (live + backtest).

Diagnóstico 2026-06-17 (243 trades en vivo):
  - 214 SELL vs 29 BUY (sesgo 7.4× a vender)
  - regime TRENDING_UP → 0W/7L : el bot VENDE dentro de tendencias alcistas
  - WR últimos 20 cierres: 5% (caída libre tras romper el oro al alza)

Causa raíz: la estrategia es de RETROCESO a Order Block (mean-reversion sobre
resistencias/soportes). En tendencia fuerte, vender la resistencia (o comprar el
soporte) en contra falla porque el precio la atraviesa. Funciona en rango.

Este módulo define UNA sola fuente de verdad para "¿esta señal va contra una
tendencia HTF fuerte?" y la usan TANTO signal_engine.analyze() (vivo) COMO
backtest/backtester.py — antes divergían (el backtester no miraba el régimen).

Sin estado, sin red, sin dependencias nuevas: solo EMAs ya calculadas en H4.
"""

import logging

import pandas as pd

from analysis.indicators import get_ema_bias
from analysis.market_structure import detect_market_structure

logger = logging.getLogger(__name__)


def compute_directional_bias(df_h4: "pd.DataFrame", lookback: int = 5) -> tuple[str, str | None]:
    """
    Bias direccional HTF — ÚNICA fuente de verdad para live y backtest.

    Antes divergían: signal_engine combinaba EMA + estructura (con fallback a
    estructura cuando la EMA era NEUTRAL), mientras el backtester usaba solo
    get_ema_bias. Resultado: el backtest no reproducía los SELL contra-tendencia
    basados en estructura que sangran en vivo. Unificado aquí.

    Returns (bias, direction):
      bias      : "BULLISH" | "BEARISH" | "NEUTRAL"
      direction : "BUY" | "SELL" | None (si NEUTRAL)
    """
    if df_h4 is None or df_h4.empty:
        return "NEUTRAL", None

    ema_bias = get_ema_bias(df_h4)
    try:
        trend_h4 = detect_market_structure(df_h4, lookback=lookback)["trend"]
    except Exception:
        trend_h4 = "NEUTRAL"

    if ema_bias == trend_h4 and ema_bias != "NEUTRAL":
        bias = ema_bias
    elif ema_bias != "NEUTRAL":
        bias = ema_bias
    elif trend_h4 != "NEUTRAL":
        bias = trend_h4
    else:
        return "NEUTRAL", None

    direction = "BUY" if bias == "BULLISH" else "SELL"
    return bias, direction


def detect_htf_trend(df_h4: "pd.DataFrame") -> str:
    """
    Tendencia de timeframe alto robusta por alineación de EMAs en H4.

    STRONG_UP   : precio > EMA200 y EMA50 > EMA200  (estructura de mercado alcista)
    STRONG_DOWN : precio < EMA200 y EMA50 < EMA200  (estructura bajista)
    NEUTRAL     : sin alineación clara (rango) — aquí el retroceso a OB SÍ funciona

    Requiere columnas ema_50 y ema_200 (las añade indicators.add_indicators).
    """
    if df_h4 is None or df_h4.empty:
        return "NEUTRAL"

    last = df_h4.iloc[-1]
    price = last.get("close")
    ema50 = last.get("ema_50")
    ema200 = last.get("ema_200")

    if any(v is None or pd.isna(v) for v in (price, ema50, ema200)):
        return "NEUTRAL"

    price, ema50, ema200 = float(price), float(ema50), float(ema200)

    if price > ema200 and ema50 > ema200:
        return "STRONG_UP"
    if price < ema200 and ema50 < ema200:
        return "STRONG_DOWN"
    return "NEUTRAL"


def counter_trend_veto(
    direction: str,
    df_h4: "pd.DataFrame",
    dxy_score: float | None = None,
    cfg: dict | None = None,
    adx: float | None = None,
) -> tuple[bool, str]:
    """
    ¿Debe vetarse esta señal por ir contra una tendencia HTF fuerte?

    Args:
        direction : "BUY" | "SELL"
        df_h4     : velas H4 con indicadores (ema_50, ema_200)
        dxy_score : opcional, score de tendencia del DXY (-1 muy bajista oro-alcista
                    .. +1 muy alcista oro-bajista). Refuerza el veto si está alineado.
        cfg       : dict config["trend_filter"]; si None, usa defaults seguros.
        adx       : opcional, ADX de H4. Habilita el veto por FUERZA de tendencia
                    (autopsia 2026-06-18: ADX alto → 0% WR; la reversión a OB
                    falla en tendencia fuerte). Si cfg.adx_veto y adx>=adx_max y
                    la señal va contra la EMA de H4 → vetar.

    Returns:
        (blocked, reason)
    """
    cfg = cfg or {}
    if not cfg.get("counter_trend_veto", True):
        return False, ""

    trend = detect_htf_trend(df_h4)

    blocked = False
    reason = ""
    if direction == "SELL" and trend == "STRONG_UP":
        blocked = True
        reason = "SELL contra tendencia HTF alcista fuerte (precio>EMA200, EMA50>EMA200)"
    elif direction == "BUY" and trend == "STRONG_DOWN":
        blocked = True
        reason = "BUY contra tendencia HTF bajista fuerte (precio<EMA200, EMA50<EMA200)"

    # ── Veto por FUERZA de tendencia (ADX) — fix autopsia 2026-06-18 ──
    # Más permisivo que STRONG_UP/DOWN: usa la EMA-bias (precio vs EMA200 +
    # EMA20 vs EMA50) y exige ADX alto. Ataca el 0% WR vendiendo en subidas.
    if cfg.get("adx_veto", False) and adx is not None and not blocked:
        adx_max = float(cfg.get("adx_max", 30.0))
        if adx >= adx_max:
            ema_dir = get_ema_bias(df_h4)
            if direction == "SELL" and ema_dir == "BULLISH":
                blocked = True
                reason = f"SELL contra EMA alcista con ADX fuerte ({adx:.0f}>={adx_max:.0f})"
            elif direction == "BUY" and ema_dir == "BEARISH":
                blocked = True
                reason = f"BUY contra EMA bajista con ADX fuerte ({adx:.0f}>={adx_max:.0f})"

    # Refuerzo opcional por DXY: si el DXY confirma la tendencia del oro en contra
    # de la señal, el veto se mantiene; si NO hay tendencia HTF clara pero el DXY
    # es extremo en contra, también vetar (config use_dxy).
    if cfg.get("use_dxy", True) and dxy_score is not None and not blocked:
        # dxy_score > 0  => DXY alcista => oro bajista (favorece SELL)
        # dxy_score < 0  => DXY bajista => oro alcista (favorece BUY)
        strong = float(cfg.get("dxy_strong", 0.8))
        if direction == "SELL" and dxy_score <= -strong and trend != "STRONG_DOWN":
            blocked = True
            reason = "SELL con DXY fuertemente bajista (oro alcista) y sin tendencia bajista HTF"
        elif direction == "BUY" and dxy_score >= strong and trend != "STRONG_UP":
            blocked = True
            reason = "BUY con DXY fuertemente alcista (oro bajista) y sin tendencia alcista HTF"

    return blocked, reason

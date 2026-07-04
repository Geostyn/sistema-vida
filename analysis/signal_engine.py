"""
Motor de señales — combina SMC, correlaciones, macro, ML ensemble y técnicas avanzadas.

Confluencias posibles (máx 16.5):
  1.  Bias H4 (EMA + estructura)                          +1.0
  2.  Order Block válido alineado                         +1.0
  3.  RSI alineado (no extremo contrario)                 +1.0 / +0.5
  4.  Sin noticias de alto impacto                        +1.0
  5.  Estructura H1 alineada (BOS/CHoCH)                  +1.0
  6.  DXY sintético alineado (correlacion inversa)        +1.5
  7.  Pares correlacionados confirman                     +1.0 / +0.5
  8.  Multi-timeframe alineado (D1+H4+H1+M15)             +1.0
  9.  Macro global alineada (VIX, yields, DXY yfinance)   +1.0 / +0.3
  10. ML ensemble confidence >= 0.60                      +1.0
  11. Volume Profile COMEX (POC/VAH/VAL GC=F)             +1.5 / +1.0
  12. Delta / Footprint (tick data MT5)                   +1.0 / +0.5 / -0.5
  13. TPO Poor High/Low (agotamiento)                     +0.5
  14. Régimen de mercado confirma dirección               +1.0
  15. Liquidity sweep reciente (stop hunt + reclaim)      +1.0
  16. Entry dentro de Fair Value Gap alineado             +0.5
  17. Estructura M15 confirma (timeframe trigger)         +0.5

Filtros duros (descartan la señal):
  - Spread actual > max_spread_atr_pct × ATR H1
  - ML veto: ensemble entrenado y proba < ml_veto_threshold
"""

import pandas as pd
import logging
from datetime import datetime, timezone, timedelta

from analysis.indicators import add_indicators, get_ema_bias, get_rsi_state
from analysis.trend_filter import counter_trend_veto
from analysis.market_structure import (
    detect_market_structure,
    find_order_blocks,
    find_liquidity_zones,
    find_fair_value_gaps,
    detect_liquidity_sweep,
    detect_displacement,
    get_significant_levels,
    find_htf_liquidity_pools,
)

logger = logging.getLogger(__name__)

MAX_CONFLUENCES = 16.5


class SignalEngine:
    def __init__(self, mt5_connector, news_feed, config: dict,
                 correlation_engine=None, macro_feed=None, learning_engine=None,
                 volume_profile=None, delta_engine=None, regime_engine=None,
                 intermarket_feed=None, neural_engine=None, quant_engine=None):
        self.mt5          = mt5_connector
        self.news         = news_feed
        self.config       = config
        self.corr         = correlation_engine
        self.macro        = macro_feed
        self.ml           = learning_engine
        self.vp           = volume_profile
        self.delta        = delta_engine
        self.regime       = regime_engine      # MarketRegimeEngine (nuevo)
        self.intermarket  = intermarket_feed   # IntermarketFeed (reales/COT/oro-plata)
        self.neural       = neural_engine      # NeuralEngine LSTM (MODO SOMBRA)
        self.quant        = quant_engine       # QuantEngine (GARCH vol + Kalman trend)
        self._last_signals: dict = {}
        self._last_swing_signals: dict = {}
        self._last_dt_signals: dict = {}   # anti-duplicado daytrade M15
        self._last_dt_bar: dict = {}       # última vela M15 analizada por símbolo
        self._last_scalp_signals: dict = {}  # anti-duplicado scalp M5
        self._last_scalp_bar: dict = {}      # última vela M5 analizada por símbolo
        # Diagnóstico: último motivo de descarte por símbolo — lo lee
        # main.py para responder /estado ("por qué no hay señal ahora")
        self.last_discard: dict = {}

    @staticmethod
    def _is_gold(symbol: str) -> bool:
        """True si el símbolo es oro. Los módulos gold-only (Volume Profile COMEX
        GC=F, DXY, intermarket/COT, sentimiento de noticias del oro) solo deben
        aportar confluencia cuando se opera oro; en forex/índices meten ruido."""
        return any(x in str(symbol).upper() for x in ("XAU", "GOLD"))

    def _discard(self, symbol: str, reason: str):
        """Registra el motivo de descarte (para /estado) y devuelve None."""
        self.last_discard[symbol] = {
            "reason": reason,
            "time":   datetime.now(timezone.utc).isoformat(),
        }
        return None

    def analyze(self, symbol: str) -> dict | None:
        """
        Análisis completo con hasta 16.5 confluencias.
        Returns señal dict si hay setup válido, None en caso contrario.
        """
        risk_cfg        = self.config.get("risk", {})
        min_rr          = risk_cfg.get("min_rr", 2.0)
        atr_mult        = risk_cfg.get("atr_sl_multiplier", 1.5)
        min_confluences = float(risk_cfg.get("min_confluences", 4.0))

        # ── 0. Régimen de mercado (NUEVO) ─────────────────────────
        regime_data = {}
        if self.regime:
            try:
                regime_data = self.regime.analyze(symbol)
                if not regime_data.get("trade_allowed", True):
                    logger.info(
                        f"[{symbol}] Régimen {regime_data.get('regime')} — "
                        f"trading bloqueado (HIGH_VOL)"
                    )
                    return self._discard(
                        symbol, f"Régimen {regime_data.get('regime')} — "
                                f"volatilidad extrema, trading bloqueado")
                # Sobrescribir min_confluences si el régimen lo exige
                override = regime_data.get("min_confluences_override")
                if override is not None:
                    min_confluences = max(min_confluences, override)
                    logger.debug(
                        f"[{symbol}] min_confluences subido a {min_confluences} "
                        f"(régimen: {regime_data.get('regime')})"
                    )
            except Exception as e:
                logger.debug(f"Regime engine no disponible: {e}")

        # ── 1. Datos H4 y H1 ─────────────────────────────────────
        df_h4 = self._get_data(symbol, "H4", 300)
        df_h1 = self._get_data(symbol, "H1", 300)
        if df_h4.empty or df_h1.empty:
            return self._discard(symbol, "Sin datos H4/H1 de MT5")

        # ── 2. Bias macro H4 (función compartida con el backtester) ──
        from analysis.trend_filter import compute_directional_bias
        bias, direction = compute_directional_bias(df_h4, lookback=5)
        if bias == "NEUTRAL" or direction is None:
            return self._discard(symbol, "Bias H4 NEUTRAL — sin dirección clara")

        # ── 2b. Puerta direccional HTF (fix sesgo 2026-06-17) ─────
        # No operar CONTRA una tendencia HTF fuerte: el diagnóstico mostró
        # 214 SELL vs 29 BUY y regime TRENDING_UP → 0W/7L (vender dentro de
        # tendencias alcistas). La estrategia de retroceso a OB solo funciona
        # en rango o A FAVOR de la tendencia, no en contra de una fuerte.
        tf_cfg = self.config.get("trend_filter", {})
        dxy_score_veto = None
        if self.corr and tf_cfg.get("use_dxy", True):
            try:
                dxy_score_veto = self.corr.get_dxy_trend().get("score")
            except Exception:
                dxy_score_veto = None
        ct_blocked, ct_reason = counter_trend_veto(
            direction, df_h4, dxy_score_veto, tf_cfg,
            adx=regime_data.get("adx"))
        if ct_blocked:
            logger.info(f"[{symbol}] [TREND-VETO] {ct_reason} — señal descartada")
            return self._discard(symbol, f"Veto tendencia HTF: {ct_reason}")

        # ── 3. Estructura y OBs en H1 ────────────────────────────
        struct_h1 = detect_market_structure(df_h1, lookback=5)
        obs       = find_order_blocks(df_h1, n_recent=40)
        liq       = find_liquidity_zones(df_h1, struct_h1["swing_highs"], struct_h1["swing_lows"])

        # ── 4. Precio, ATR, RSI ───────────────────────────────────
        tick = self.mt5.get_current_price(symbol)
        if not tick:
            return self._discard(symbol, "Sin precio actual de MT5")

        price = float(tick["ask"]) if direction == "BUY" else float(tick["bid"])
        atr   = df_h1["atr"].iloc[-1] if "atr" in df_h1.columns else None
        if atr is None or pd.isna(atr) or float(atr) == 0:
            return self._discard(symbol, "ATR no disponible")
        atr       = float(atr)

        # Filtro de volatilidad por PERCENTIL de ATR (validado backtest in-sample
        # + OOS: PF 1.45→1.67, Sharpe 2.3→3.2, DD igual; research mean-reversion +
        # autopsia atr_pct r=-0.37). En vol extrema la reversión a OB falla.
        vol_cfg = self.config.get("volatility", {})
        if vol_cfg.get("vol_filter", False):
            vw = int(vol_cfg.get("vol_window", 150))
            atr_hist = df_h1["atr"].tail(vw)
            vmax = float(vol_cfg.get("vol_pct_max", 0.90))
            if len(atr_hist) >= 30 and float((atr_hist <= atr).mean()) >= vmax:
                return self._discard(
                    symbol, f"Volatilidad extrema (ATR ≥ P{vmax*100:.0f}) — "
                            f"la reversión a OB falla en alta vol")

        rsi_state = get_rsi_state(df_h1)
        rsi_val   = float(df_h1["rsi"].iloc[-1]) if "rsi" in df_h1.columns else None

        # ── 4a. Quant: GARCH (vol prevista) + Kalman (tendencia) ──
        garch_vol    = 1.0
        kalman_slope = 0.0
        if self.quant:
            try:
                q = self.quant.analyze(symbol, df_h1, atr=atr)
                garch_vol    = float(q.get("garch_vol", 1.0))
                kalman_slope = float(q.get("kalman_slope", 0.0))
                # Guard de volatilidad: si se prevé vol alta, exigir más calidad
                q_cfg = self.config.get("quant", {})
                if q_cfg.get("garch_vol_guard", True) and \
                   garch_vol >= float(q_cfg.get("garch_high_ratio", 1.6)):
                    bump = float(q_cfg.get("garch_conf_bump", 1.0))
                    min_confluences += bump
                    logger.debug(
                        f"[{symbol}] GARCH vol alta ({garch_vol:.2f}) — "
                        f"min_confluences +{bump} → {min_confluences}"
                    )
            except Exception as e:
                logger.debug(f"Quant engine no disponible: {e}")

        # ── 4b. Filtro de spread (coste de entrada) ───────────────
        spread         = float(tick.get("spread", 0) or 0)
        max_spread_pct = float(risk_cfg.get("max_spread_atr_pct", 0.12))
        if spread > 0 and spread > atr * max_spread_pct:
            logger.info(
                f"[{symbol}] Spread {spread:.2f} > {max_spread_pct:.0%} ATR "
                f"({atr:.2f}) — señal descartada (coste excesivo)"
            )
            return self._discard(
                symbol, f"Spread {spread:.2f} excesivo (> {max_spread_pct:.0%} ATR)")

        # ── 4c. Protección de liquidez (fix 11-jun) ───────────────
        # Nunca operar contra un barrido+reclaim ni contra un impulso
        # institucional reciente — el bonus de sweep A FAVOR sigue intacto
        liq_cfg = self.config.get("liquidity", {})
        extra_warnings = []

        if liq_cfg.get("sweep_against_veto", False):
            opposite = "SELL" if direction == "BUY" else "BUY"
            # Solo niveles significativos (equal H/L, PDH/PDL, sesión):
            # barrer un swing menor no es un liquidity grab institucional
            sig_levels = get_significant_levels(df_h1)
            lvls = sig_levels["lows"] if opposite == "BUY" else sig_levels["highs"]
            sweep_against = {"swept": False}
            if lvls:
                sweep_against = detect_liquidity_sweep(
                    df_h1, struct_h1["swing_highs"], struct_h1["swing_lows"],
                    opposite, lookback=int(liq_cfg.get("sweep_against_lookback", 6)),
                    levels=lvls,
                )
            if sweep_against["swept"]:
                mode = liq_cfg.get("sweep_against_mode", "veto")
                if mode == "raise":
                    bump = float(liq_cfg.get("raise_confluences_by", 2.0))
                    min_confluences += bump
                    extra_warnings.append(
                        f"⚠️ Barrido {opposite} reciente en {sweep_against['level']:.2f} "
                        f"— umbral +{bump:.0f}"
                    )
                    logger.info(
                        f"[{symbol}] [LIQ-RAISE] Sweep contra {direction} en "
                        f"{sweep_against['level']:.2f} — min_confluences +{bump:.0f}"
                    )
                else:
                    logger.info(
                        f"[{symbol}] [LIQ-VETO] Sweep+reclaim contra {direction} en "
                        f"{sweep_against['level']:.2f} (hace {sweep_against['bars_ago']} velas) "
                        f"— señal descartada"
                    )
                    return self._discard(
                        symbol, f"Veto liquidez: barrido contra {direction} en "
                                f"{sweep_against['level']:.2f}")

        if liq_cfg.get("displacement_filter", False):
            disp = detect_displacement(
                df_h1, atr,
                n_candles=int(liq_cfg.get("displacement_candles", 3)),
                body_atr=float(liq_cfg.get("displacement_body_atr", 0.8)),
                net_atr=float(liq_cfg.get("displacement_net_atr", 1.5)),
                exclude_last=True,
            )
            against = (disp["direction"] == "BULLISH" and direction == "SELL") or \
                      (disp["direction"] == "BEARISH" and direction == "BUY")
            if against:
                logger.info(
                    f"[{symbol}] [LIQ-VETO] Desplazamiento {disp['direction']} contra "
                    f"{direction} ({disp['impulse_candles']} velas impulso, "
                    f"neto {disp['net_move_atr']:+.1f} ATR) — señal descartada"
                )
                return self._discard(
                    symbol, f"Veto liquidez: impulso {disp['direction']} reciente "
                            f"contra {direction}")

        # ── 4d. Veto por liquidez HTF intacta (caso 11-jun) ────────
        # No operar DE FRENTE contra un pool de liquidez H4/D1 sin barrer:
        # el precio actúa como imán hacia esos niveles (equal highs, máximos
        # previos) y suele barrerlos ANTES de girar. Si el pool YA fue
        # barrido+reclaimed en H1, el setup es válido (sweep → reversión).
        htf_liq_dist_atr = 99.0  # feature ML: 99 = sin pool intacto en contra
        if liq_cfg.get("htf_liquidity_veto", True):
            try:
                df_d1 = self._get_data(symbol, "D1", 90)
            except Exception:
                df_d1 = pd.DataFrame()
            pools = find_htf_liquidity_pools(
                [(df_h4, "H4"), (df_d1, "D1")], price, atr,
                max_dist_atr=float(liq_cfg.get("htf_liquidity_atr", 1.5)),
                swing_lookback=int(liq_cfg.get("htf_swing_lookback", 3)),
            )
            in_path = pools["above"] if direction == "SELL" else pools["below"]
            if in_path:
                nearest = in_path[0]
                htf_liq_dist_atr = round(abs(nearest["price"] - price) / atr, 2)
                # ¿Ese pool ya fue barrido en H1 (mecha fuera + cierre dentro)?
                sweep_done = detect_liquidity_sweep(
                    df_h1, struct_h1["swing_highs"], struct_h1["swing_lows"],
                    direction,
                    lookback=int(liq_cfg.get("sweep_against_lookback", 6)),
                    levels=[p["price"] for p in in_path],
                )
                if not sweep_done["swept"]:
                    logger.info(
                        f"[{symbol}] [LIQ-VETO-HTF] {direction} contra pool "
                        f"{nearest['kind']} {nearest['tf']} en {nearest['price']:.2f} "
                        f"({htf_liq_dist_atr:.1f} ATR) INTACTO — el mercado suele "
                        f"barrer esa liquidez antes de girar — señal descartada"
                    )
                    return self._discard(
                        symbol, f"Veto liquidez HTF: {direction} contra "
                                f"{nearest['kind']} {nearest['tf']} intacto en "
                                f"{nearest['price']:.2f} ({htf_liq_dist_atr:.1f} ATR)")

        # ── 5. Buscar OB válido ───────────────────────────────────
        target_type = "BULLISH" if direction == "BUY" else "BEARISH"
        ob_target   = self._find_nearest_ob(obs, price, direction, atr, target_type)
        if ob_target is None:
            return self._discard(
                symbol, f"Sin Order Block {('BULLISH' if direction == 'BUY' else 'BEARISH')} "
                        f"válido cerca del precio")

        # ── 6. Niveles de trading (entry en retroceso al OB) ─────
        entry, sl, tp1, tp2, entry_type = self._calculate_levels(
            direction, price, ob_target, atr, atr_mult, struct_h1, liq,
            regime=regime_data.get("regime", "UNKNOWN"),
        )
        if entry is None:
            return self._discard(symbol, "Niveles de entrada no calculables")

        risk = abs(entry - sl)
        if risk == 0:
            return self._discard(symbol, "Riesgo cero (entry = SL)")
        rr = abs(tp1 - entry) / risk
        if rr < min_rr:
            return self._discard(
                symbol, f"R:R {rr:.1f} insuficiente (mínimo {min_rr})")

        # ── 7. Noticias ───────────────────────────────────────────
        avoid_min  = self.config.get("sessions", {}).get("avoid_news_minutes", 30)
        news_stat  = self.news.is_news_blackout(minutes_buffer=avoid_min)
        news_warn  = news_stat["reason"] if news_stat["blackout"] else ""

        # Contexto de noticias del día para incluir en la señal
        try:
            news_calendar = self.news.get_daily_summary()
        except Exception:
            news_calendar = []

        # ── 8. CORRELACIONES ──────────────────────────────────────
        corr_context = None
        dxy_aligned  = False
        pairs_score  = 0.0
        mtf_aligned  = False

        if self.corr and symbol in ("XAUUSD", "XAUEUR"):
            try:
                corr_context = self.corr.get_full_context(symbol, direction)
                dxy_aligned  = corr_context["dxy_aligned"]
                pairs_score  = corr_context["pairs"]["score"]
                mtf_aligned  = corr_context["mtf_aligned"]
            except Exception as e:
                logger.debug(f"Correlaciones no disponibles: {e}")

        # ── 9. MACRO GLOBAL ───────────────────────────────────────
        macro_data    = {}
        macro_bias    = None
        macro_aligned = False
        if self.macro:
            try:
                macro_data   = self.macro.get_macro_bias()
                macro_bias   = macro_data.get("gold_bias", "NEUTRAL")
                expected_mac = "BULLISH" if direction == "BUY" else "BEARISH"
                macro_aligned = macro_bias == expected_mac
            except Exception as e:
                logger.debug(f"Macro feed no disponible: {e}")

        # ── 10. Confluencias base (1-9) ───────────────────────────
        confluences = 0.0
        cf_details  = []
        cf_details.extend(extra_warnings)  # avisos de liquidez (modo "raise")

        confluences += 1;     cf_details.append(f"Bias H4 {bias}")

        if ob_target["valid"]:
            confluences += 1; cf_details.append(f"OB {ob_target['type']} valido")

        if (direction == "BUY"  and rsi_state == "OVERSOLD") or \
           (direction == "SELL" and rsi_state == "OVERBOUGHT"):
            confluences += 1; cf_details.append(f"RSI {rsi_state}")
        elif rsi_state == "NEUTRAL":
            confluences += 0.5

        if not news_stat["blackout"]:
            confluences += 1; cf_details.append("Sin noticias")

        last_ev = struct_h1.get("last_event")
        if last_ev and last_ev["direction"] == bias:
            confluences += 1; cf_details.append(f"{last_ev['type']} H1")

        if dxy_aligned and corr_context:
            confluences += 1.5; cf_details.append(f"DXY alineado {corr_context['dxy']['trend']}")

        if pairs_score >= 0.6:
            confluences += 1;   cf_details.append(f"Pares confirmados ({pairs_score:.0%})")
        elif pairs_score >= 0.4:
            confluences += 0.5

        if mtf_aligned and corr_context:
            mtf_info = corr_context["mtf"]
            confluences += 1; cf_details.append(
                f"Multi-TF {mtf_info.get('bullish_tfs',0)}B/{mtf_info.get('bearish_tfs',0)}B"
            )

        if macro_aligned:
            confluences += 1; cf_details.append(f"Macro global {macro_bias}")
        elif macro_bias == "NEUTRAL":
            confluences += 0.3

        # ── 9b. INTERMARKET (reales 10Y + COT + oro/plata + riesgo) ──
        # Datos que NO derivan del precio del oro → rompen la circularidad de
        # las confluencias técnicas (driver #1: rendimientos reales).
        inter_ctx   = None
        inter_conf  = 0.0
        inter_score = 0.0
        if self.intermarket:
            try:
                inter_ctx   = self.intermarket.get_intermarket_score(direction)
                inter_conf  = float(inter_ctx.get("confluence", 0.0))
                inter_score = float(inter_ctx.get("gold_bias_score", 0.0))
                if inter_conf > 0:
                    confluences += inter_conf
                    for d in inter_ctx.get("details", []):
                        cf_details.append(d)
            except Exception as e:
                logger.debug(f"Intermarket no disponible: {e}")

        # ── 11. Volume Profile COMEX ──────────────────────────────
        vp_score, vp_desc = 0.0, ""
        if self.vp:
            try:
                vp_score, vp_desc = self.vp.get_confluence(
                    price=price, direction=direction, atr=atr, spot_price=price
                )
                if vp_score > 0:
                    confluences += vp_score
                    cf_details.append(vp_desc)
            except Exception as e:
                logger.debug(f"Volume Profile no disponible: {e}")

        # ── 12. Delta / Footprint ─────────────────────────────────
        delta_score, delta_desc = 0.0, ""
        if self.delta:
            try:
                delta_score, delta_desc = self.delta.get_confluence(
                    symbol=symbol, direction=direction, df_h1=df_h1
                )
                if delta_score != 0:
                    confluences += delta_score
                    if delta_desc:
                        cf_details.append(delta_desc)
            except Exception as e:
                logger.debug(f"Delta engine no disponible: {e}")

        # ── 13. TPO Poor High/Low ─────────────────────────────────
        tpo_score = self._detect_tpo_weakness(df_h1, direction)
        if tpo_score > 0:
            confluences += tpo_score
            cf_details.append(f"TPO poor {'high' if direction == 'SELL' else 'low'} (agotamiento)")

        # ── 14. Régimen confirma dirección (NUEVO) ─────────────────
        regime_bonus = 0.0
        regime_str   = regime_data.get("regime", "UNKNOWN")
        if regime_str == "TRENDING_UP" and direction == "BUY":
            regime_bonus = 1.0
            cf_details.append(f"Régimen TRENDING_UP confirma BUY (ADX {regime_data.get('adx',0):.0f})")
        elif regime_str == "TRENDING_DOWN" and direction == "SELL":
            regime_bonus = 1.0
            cf_details.append(f"Régimen TRENDING_DOWN confirma SELL (ADX {regime_data.get('adx',0):.0f})")
        elif regime_str == "RANGING":
            cf_details.append(f"Régimen RANGING — umbral subido (ADX {regime_data.get('adx',0):.0f})")
        confluences += regime_bonus

        # ── 15. Liquidity Sweep (stop hunt + reclaim) ─────────────
        sweep_score = 0.0
        sweep = detect_liquidity_sweep(
            df_h1, struct_h1["swing_highs"], struct_h1["swing_lows"], direction
        )
        if sweep["swept"]:
            sweep_score = 1.0
            confluences += sweep_score
            cf_details.append(
                f"Liquidity sweep en {sweep['level']:.2f} "
                f"(hace {sweep['bars_ago']} velas)"
            )

        # ── 16. Fair Value Gap alineado ───────────────────────────
        fvg_score = 0.0
        try:
            fvgs = find_fair_value_gaps(df_h1, n_recent=40)
            for gap in fvgs:
                if not gap["valid"] or gap["type"] != target_type:
                    continue
                # Entry dentro del gap o a menos de 0.5 ATR de su midpoint
                if gap["bottom"] <= entry <= gap["top"] or \
                   abs(entry - gap["midpoint"]) <= atr * 0.5:
                    fvg_score = 0.5
                    confluences += fvg_score
                    cf_details.append(
                        f"FVG {gap['type']} {gap['bottom']:.2f}-{gap['top']:.2f}"
                    )
                    break
        except Exception as e:
            logger.debug(f"FVG no disponible: {e}")

        # ── 17. Confirmación M15 (timeframe trigger) ──────────────
        m15_aligned = 0
        df_m15 = pd.DataFrame()  # se reutiliza para el refinamiento funded
        try:
            df_m15 = self._get_data(symbol, "M15", 200)
            if not df_m15.empty:
                struct_m15 = detect_market_structure(df_m15, lookback=5)
                ev_m15     = struct_m15.get("last_event")
                if struct_m15["trend"] == bias or \
                   (ev_m15 and ev_m15["direction"] == bias):
                    m15_aligned = 1
                    confluences += 0.5
                    cf_details.append(f"M15 confirma ({struct_m15['trend']})")
        except Exception as e:
            logger.debug(f"M15 no disponible: {e}")

        # Confidence base
        confidence = min(confluences / MAX_CONFLUENCES, 1.0)

        # ── 15. ML Ensemble (SIEMPRE AL FINAL — necesita vp/delta/regime) ──
        ml_proba = 0.5
        signal_preview = {
            # Features base
            "direction":      direction,
            "confluences":    int(confluences),
            "confidence":     confidence,
            "rsi_state":      rsi_state,
            "bias_h4":        bias,
            "ob_type":        ob_target["type"],
            "news_blackout":  int(news_stat["blackout"]),
            # Features nuevas
            "hour_utc":       datetime.now(timezone.utc).hour,
            "day_of_week":    datetime.now(timezone.utc).weekday(),
            "vp_score":       float(vp_score),
            "delta_score":    float(delta_score),
            "atr_pct":        round(atr / price * 100, 3) if price > 0 else 0,
            "hurst":          float(regime_data.get("hurst", 0.5)),
            "adx":            float(regime_data.get("adx", 20)),
            "pairs_score":    float(pairs_score),
            "sweep_score":    float(sweep_score),
            "fvg_score":      float(fvg_score),
            "m15_aligned":    int(m15_aligned),
            "htf_liq_dist":   float(htf_liq_dist_atr),
            # Features intermarket (Fase 2): datos macro que no derivan del precio
            "inter_score":    float(inter_score),
            "real_yield_imp": float(inter_ctx["real_yields"].get("gold_impact", 0.0)) if inter_ctx else 0.0,
            "cot_impact":     float(inter_ctx["cot"].get("gold_impact", 0.0)) if inter_ctx else 0.0,
            "cot_percentile": float(inter_ctx["cot"].get("percentile", 0.5)) if inter_ctx else 0.5,
            # Quant (Fase 3): GARCH vol prevista + pendiente Kalman
            "garch_vol":      float(garch_vol),
            "kalman_slope":   float(kalman_slope),
            "timestamp":      datetime.now(timezone.utc).isoformat(),
        }
        # Voto del ML gateado por config (ml.vote_enabled). Autopsia 2026-06-18:
        # ml_proba correlaciona NEGATIVO con ganar (r=-0.267) → el modelo está
        # invertido (entrenado sobre datos sesgados SELL-en-subida). Mientras esté
        # mal calibrado NO debe votar: se calcula y registra, pero no suma ni veta.
        ml_vote = self.config.get("ml", {}).get("vote_enabled", True)
        if self.ml:
            try:
                ml_proba = self.ml.predict_win_probability(signal_preview)
                if ml_vote:
                    # Threshold bajado a 0.60 (ensemble es más robusto que RF solo)
                    if ml_proba >= 0.60:
                        confluences += 1; cf_details.append(f"ML ensemble confirma ({ml_proba:.0%})")
                    confidence = min(confluences / MAX_CONFLUENCES, 1.0)

                    # ML VETO: con modelo entrenado en 40+ trades, descartar
                    # señales que el ensemble considera claramente perdedoras
                    veto_thr = float(risk_cfg.get("ml_veto_threshold", 0.40))
                    if getattr(self.ml, "last_count", 0) >= 40 and ml_proba < veto_thr:
                        logger.info(
                            f"[{symbol}] ML VETO — proba {ml_proba:.0%} < {veto_thr:.0%} "
                            f"({self.ml.last_count} trades de historial)"
                        )
                        return self._discard(
                            symbol, f"ML veto: probabilidad {ml_proba:.0%} < {veto_thr:.0%}")
            except Exception as e:
                logger.debug(f"ML no disponible: {e}")

        # ── Red neuronal LSTM ─────────────────────────────────────
        # Por defecto MODO SOMBRA: predice y se REGISTRA, pero NO influye.
        # Si neural.influence=true en config (tras validar con
        # backtest/neural_eval.py), la red SÍ modifica la señal:
        #   · +neural.conf_bonus confluencias si proba >= neural.conf_threshold
        #   · veto si neural.veto_enabled y proba < neural.veto_threshold
        neural_proba = 0.5
        if self.neural is not None and getattr(self.neural, "available", lambda: False)():
            try:
                neural_proba = self.neural.predict_proba(df_h1, direction)
            except Exception as e:
                logger.debug(f"Neural no disponible: {e}")

        n_cfg = self.config.get("neural", {})
        if n_cfg.get("influence", False) and self.neural and self.neural.available():
            if neural_proba >= float(n_cfg.get("conf_threshold", 0.58)):
                bonus = float(n_cfg.get("conf_bonus", 0.5))
                confluences += bonus
                cf_details.append(f"Red neuronal confirma ({neural_proba:.0%})")
            if n_cfg.get("veto_enabled", False):
                veto_thr = float(n_cfg.get("veto_threshold", 0.40))
                if neural_proba < veto_thr:
                    logger.info(
                        f"[{symbol}] NEURAL VETO — proba {neural_proba:.0%} "
                        f"< {veto_thr:.0%}"
                    )
                    return self._discard(
                        symbol, f"Veto red neuronal: {neural_proba:.0%} < {veto_thr:.0%}")

        # Confidence final
        confidence = min(confluences / MAX_CONFLUENCES, 1.0)

        # Mínimo de confluencias
        if confluences < min_confluences:
            logger.debug(
                f"[{symbol}] {confluences:.1f} confluencias — descartado "
                f"(min {min_confluences}, régimen: {regime_str})"
            )
            return self._discard(
                symbol, f"{confluences:.1f} confluencias < mínimo "
                        f"{min_confluences} (régimen {regime_str})")

        if self._is_duplicate(symbol, direction, entry, atr, ob=ob_target):
            return self._discard(
                symbol, f"Duplicada: misma idea {direction} ya enviada "
                        f"(mismo OB / entry sin cambio)")

        # ── Reasoning y news context ──────────────────────────────
        from analysis.reasoning_engine import generate_reasoning, generate_news_context
        try:
            reasoning    = generate_reasoning(signal_preview | {
                "ob_type":   ob_target["type"],
                "rsi_value": rsi_val,
                "rr":        rr,
                "confluence_details": cf_details,
            }, macro_data, regime_data)
        except Exception:
            reasoning = ""

        try:
            news_context_lines = generate_news_context(news_calendar)
        except Exception:
            news_context_lines = ""

        # ── Construir señal completa ──────────────────────────────
        signal = {
            "symbol":             symbol,
            "direction":          direction,
            "entry":              round(entry, 5),
            "sl":                 round(sl, 5),
            "tp1":                round(tp1, 5),
            "tp2":                round(tp2, 5) if tp2 else None,
            "rr":                 round(rr, 2),
            "confidence":         round(confidence, 2),
            "confluences":        round(confluences, 1),
            "confluence_details": cf_details,
            "timeframe":          "H1",
            "bias_h4":            bias,
            "structure_h1":       struct_h1["trend"],
            "ob_type":            ob_target["type"],
            "rsi_state":          rsi_state,
            "rsi_value":          round(rsi_val, 1) if rsi_val else None,
            "news_warning":       news_warn,
            "news_blackout":      news_stat["blackout"],
            "news_context":       news_context_lines,
            "dxy_aligned":        dxy_aligned,
            "pairs_score":        round(pairs_score, 2),
            "mtf_aligned":        mtf_aligned,
            "macro_bias":         macro_bias,
            "ml_proba":           round(ml_proba, 2),
            "regime":             regime_str,
            "regime_adx":         float(regime_data.get("adx", 20)),
            "regime_hurst":       float(regime_data.get("hurst", 0.5)),
            "vp_score":           float(vp_score),
            "delta_score":        float(delta_score),
            "sweep_score":        float(sweep_score),
            "fvg_score":          float(fvg_score),
            "m15_aligned":        int(m15_aligned),
            "htf_liq_dist":       float(htf_liq_dist_atr),
            "ob_midpoint":        float(ob_target["midpoint"]),
            "atr_pct":            round(atr / price * 100, 3) if price > 0 else 0,
            "entry_type":         entry_type,
            "reasoning":          reasoning,
            # Contexto intermarket para Telegram (reales/COT/oro-plata) y features
            "intermarket":        inter_ctx,
            "inter_score":        float(inter_score),
            # Red neuronal LSTM (modo sombra) — solo informativo/registro
            "neural_proba":       round(float(neural_proba), 3),
            # Quant (Fase 3): GARCH vol prevista + pendiente Kalman
            "garch_vol":          round(float(garch_vol), 3),
            "kalman_slope":       round(float(kalman_slope), 3),
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "atr":                round(atr, 5),
        }

        # ── Niveles refinados M15 (bloque FundedNext 2K) ──────────
        # La demo conserva los niveles H1 (backtest validado); el SL corto
        # M15 va solo en signal["funded_levels"] para la cuenta de 2K
        if self.config.get("funded", {}).get("m15_refine", False):
            try:
                refined = self._refine_levels_m15(
                    direction, ob_target, entry, atr, df_m15
                )
                if refined:
                    signal["funded_levels"] = refined
            except Exception as e:
                logger.debug(f"M15 refine no disponible: {e}")

        self._last_signals[symbol] = signal
        logger.info(
            f"[{symbol}] SENAL {direction} | Entry:{entry:.5f} SL:{sl:.5f} TP1:{tp1:.5f} | "
            f"R:R:{rr:.1f} | Conf:{confidence:.0%} ({confluences:.1f}/{MAX_CONFLUENCES}) | "
            f"ML:{ml_proba:.0%} | Régimen:{regime_str}"
        )
        return signal

    # ── Helpers ────────────────────────────────────────────────────

    def _get_data(self, symbol, timeframe, n_bars):
        df = self.mt5.get_rates(symbol, timeframe, n_bars)
        return add_indicators(df) if not df.empty else df

    def _find_nearest_ob(self, obs, price, direction, atr, target_type):
        for ob in obs:
            if not ob["valid"] or ob["type"] != target_type:
                continue
            if direction == "BUY":
                if 0 <= (price - ob["top"]) <= atr * 2 or ob["bottom"] <= price <= ob["top"]:
                    return ob
            else:
                if 0 <= (ob["bottom"] - price) <= atr * 2 or ob["bottom"] <= price <= ob["top"]:
                    return ob
        return None

    def _calculate_levels(self, direction, price, ob, atr, atr_mult,
                          structure, liq, regime: str = "UNKNOWN"):
        """
        Entry en RETROCESO al Order Block (no al precio de mercado):
          - Si el precio aún no llegó al OB → LIMIT en el borde del OB.
            Mejor precio de entrada + SL más cercano = mayor R:R real.
          - Si el precio ya está dentro del OB → entry al precio actual.

        TP2 dinámico según régimen:
          - TRENDING a favor → runner a 4R (dejar correr la tendencia)
          - RANGING          → 2.5R (tomar beneficio antes del giro)
          - resto            → 3R
        """
        trending_with = (
            (regime == "TRENDING_UP"   and direction == "BUY") or
            (regime == "TRENDING_DOWN" and direction == "SELL")
        )
        tp2_r = 4.0 if trending_with else (2.5 if regime == "RANGING" else 3.0)

        if direction == "BUY":
            inside_ob = ob["bottom"] <= price <= ob["top"]
            if inside_ob:
                entry, entry_type = price, "MARKET"
            else:
                entry, entry_type = ob["top"], "RETRACEMENT"
            sl   = ob["bottom"] - (atr * atr_mult)
            risk = abs(entry - sl)
            if risk == 0:
                return None, None, None, None, None
            next_high = structure.get("last_high")
            tp1 = next_high["price"] if next_high and next_high["price"] > entry + risk * 2 \
                else entry + risk * 2
            tp2 = next((z["price"] for z in liq.get("buy_side", []) if z["price"] > tp1),
                       entry + risk * tp2_r)
            tp2 = max(tp2, entry + risk * tp2_r) if trending_with else tp2
        else:
            inside_ob = ob["bottom"] <= price <= ob["top"]
            if inside_ob:
                entry, entry_type = price, "MARKET"
            else:
                entry, entry_type = ob["bottom"], "RETRACEMENT"
            sl   = ob["top"] + (atr * atr_mult)
            risk = abs(sl - entry)
            if risk == 0:
                return None, None, None, None, None
            next_low = structure.get("last_low")
            tp1 = next_low["price"] if next_low and next_low["price"] < entry - risk * 2 \
                else entry - risk * 2
            tp2 = next((z["price"] for z in liq.get("sell_side", []) if z["price"] < tp1),
                       entry - risk * tp2_r)
            tp2 = min(tp2, entry - risk * tp2_r) if trending_with else tp2

        return entry, sl, tp1, tp2, entry_type

    def _refine_levels_m15(self, direction: str, ob_h1: dict, entry_h1: float,
                           atr_h1: float, df_m15: "pd.DataFrame") -> dict | None:
        """
        Refina entry/SL sobre estructura M15 DENTRO de la zona del OB H1.
        Objetivo: SL ~$10-20 en vez de $25-50 — en una cuenta de 2K con
        lote mínimo 0.01 (= $1/punto en XAUUSD) el SL en $ ES el riesgo en $.

        Entry = borde del OB M15 más cercano al entry H1, acotado dentro
        del OB H1 (la LIMIT manual se llena en el mismo retroceso que la demo).
        SL = extremo del OB M15 -/+ 0.5 x ATR M15, con suelo de 0.3 x ATR H1.
        """
        if df_m15 is None or df_m15.empty or "atr" not in df_m15.columns:
            return None
        atr_m15 = df_m15["atr"].iloc[-1]
        if pd.isna(atr_m15) or float(atr_m15) <= 0:
            return None
        atr_m15 = float(atr_m15)

        target_type = "BULLISH" if direction == "BUY" else "BEARISH"
        zone_lo, zone_hi = float(ob_h1["bottom"]), float(ob_h1["top"])

        candidates = [
            ob for ob in find_order_blocks(df_m15, n_recent=60)
            if ob["type"] == target_type and ob["valid"]
            and not (ob["top"] < zone_lo or ob["bottom"] > zone_hi)  # solapa OB H1
        ]
        if not candidates:
            return None

        best     = min(candidates, key=lambda o: abs(o["midpoint"] - entry_h1))
        sl_floor = 0.3 * atr_h1  # distancia mínima del stop (evitar ruido M15)

        if direction == "BUY":
            entry = min(max(float(best["top"]), zone_lo), zone_hi)
            sl    = float(best["bottom"]) - atr_m15 * 0.5
            if entry - sl < sl_floor:
                sl = entry - sl_floor
            if sl <= 0 or entry <= sl:
                return None
        else:
            entry = min(max(float(best["bottom"]), zone_lo), zone_hi)
            sl    = float(best["top"]) + atr_m15 * 0.5
            if sl - entry < sl_floor:
                sl = entry + sl_floor
            if entry >= sl:
                return None

        return {
            "entry":   round(entry, 2),
            "sl":      round(sl, 2),
            "sl_dist": round(abs(entry - sl), 2),
            "refined": True,
        }

    def _detect_tpo_weakness(self, df: "pd.DataFrame", direction: str) -> float:
        if len(df) < 5:
            return 0.0
        recent      = df.tail(5)
        recent_high = float(recent["high"].max())
        recent_low  = float(recent["low"].min())
        tol         = 0.0002

        touches_high = int((recent["high"] >= recent_high * (1 - tol)).sum())
        touches_low  = int((recent["low"]  <= recent_low  * (1 + tol)).sum())

        if direction == "SELL" and touches_high == 1:
            return 0.5
        if direction == "BUY"  and touches_low  == 1:
            return 0.5
        return 0.0

    # ── Swing Trading ──────────────────────────────────────────────────

    def analyze_swing(self, symbol: str) -> dict | None:
        """
        Swing trade: D1 bias → H4 OBs → H1 trigger.
        Señales de menor frecuencia (1-3/semana), R:R 1:2.5+ y hold estimado
        2-7 días. Corre en paralelo con las señales intraday y se distingue
        en Telegram con el header SWING.
        """
        swing_cfg       = self.config.get("swing", {})
        risk_cfg        = self.config.get("risk", {})
        min_rr          = float(swing_cfg.get("min_rr", 2.5))
        atr_mult        = float(swing_cfg.get("atr_sl_multiplier", 2.0))
        min_confluences = float(swing_cfg.get("min_confluences", 6.0))

        # ── 0. Régimen ─────────────────────────────────────────────
        regime_data = {}
        if self.regime:
            try:
                regime_data = self.regime.analyze(symbol)
                if not regime_data.get("trade_allowed", True):
                    return self._discard(
                        symbol + "_sw",
                        f"[SWING] Régimen {regime_data.get('regime')} — bloqueado")
                override = regime_data.get("min_confluences_override")
                if override is not None:
                    min_confluences = max(min_confluences, override + 1.0)
            except Exception as e:
                logger.debug(f"Regime swing: {e}")

        # ── 1. Datos D1, H4, H1 ────────────────────────────────────
        df_d1 = self._get_data(symbol, "D1", 300)
        df_h4 = self._get_data(symbol, "H4", 300)
        df_h1 = self._get_data(symbol, "H1", 200)
        if df_d1.empty or df_h4.empty:
            return self._discard(symbol + "_sw", "[SWING] Sin datos D1/H4")

        # ── 2. Bias macro D1 ───────────────────────────────────────
        ema_bias_d1 = get_ema_bias(df_d1)
        struct_d1   = detect_market_structure(df_d1, lookback=5)
        trend_d1    = struct_d1["trend"]

        if ema_bias_d1 == trend_d1 and ema_bias_d1 != "NEUTRAL":
            bias = ema_bias_d1
        elif ema_bias_d1 != "NEUTRAL":
            bias = ema_bias_d1
        elif trend_d1 != "NEUTRAL":
            bias = trend_d1
        else:
            return self._discard(symbol + "_sw", "[SWING] Bias D1 NEUTRAL — sin dirección clara")

        direction = "BUY" if bias == "BULLISH" else "SELL"

        # ── 3. Estructura H4 y OBs ─────────────────────────────────
        struct_h4 = detect_market_structure(df_h4, lookback=5)
        obs_h4    = find_order_blocks(df_h4, n_recent=40)
        liq_h4    = find_liquidity_zones(
            df_h4, struct_h4["swing_highs"], struct_h4["swing_lows"])

        # ── 4. Precio, ATR H4, RSI H4 ──────────────────────────────
        tick = self.mt5.get_current_price(symbol)
        if not tick:
            return self._discard(symbol + "_sw", "[SWING] Sin precio MT5")

        price   = float(tick["ask"]) if direction == "BUY" else float(tick["bid"])
        atr_h4  = df_h4["atr"].iloc[-1] if "atr" in df_h4.columns else None
        if atr_h4 is None or pd.isna(atr_h4) or float(atr_h4) == 0:
            return self._discard(symbol + "_sw", "[SWING] ATR H4 no disponible")
        atr_h4    = float(atr_h4)
        rsi_state = get_rsi_state(df_h4)
        rsi_val   = float(df_h4["rsi"].iloc[-1]) if "rsi" in df_h4.columns else None

        # Spread: más tolerante para swing (ATR H4 >> ATR H1)
        spread         = float(tick.get("spread", 0) or 0)
        max_spread_pct = float(risk_cfg.get("max_spread_atr_pct", 0.12)) * 2
        if spread > 0 and spread > atr_h4 * max_spread_pct:
            return self._discard(
                symbol + "_sw",
                f"[SWING] Spread {spread:.2f} excesivo (>{max_spread_pct:.0%} ATR H4)")

        # ── 4c. Veto desplazamiento H4 ─────────────────────────────
        liq_cfg        = self.config.get("liquidity", {})
        extra_warnings = []

        if liq_cfg.get("displacement_filter", False):
            disp = detect_displacement(
                df_h4, atr_h4,
                n_candles=int(liq_cfg.get("displacement_candles", 3)),
                body_atr=float(liq_cfg.get("displacement_body_atr", 0.8)),
                net_atr=float(liq_cfg.get("displacement_net_atr", 1.5)),
                exclude_last=True,
            )
            against = (
                (disp["direction"] == "BULLISH" and direction == "SELL") or
                (disp["direction"] == "BEARISH" and direction == "BUY")
            )
            if against:
                return self._discard(
                    symbol + "_sw",
                    f"[SWING] Veto: impulso {disp['direction']} H4 contra {direction}")

        # ── 4d. Veto HTF D1/W1 intacto ─────────────────────────────
        htf_liq_dist_atr = 99.0
        try:
            df_w1  = self._get_data(symbol, "W1", 52)
            frames = [(df_d1, "D1")]
            if not df_w1.empty:
                frames.insert(0, (df_w1, "W1"))
            pools = find_htf_liquidity_pools(
                frames, price, atr_h4,
                max_dist_atr=float(liq_cfg.get("htf_liquidity_atr", 1.5)) * 2,
                swing_lookback=int(liq_cfg.get("htf_swing_lookback", 3)),
            )
            in_path = pools["above"] if direction == "SELL" else pools["below"]
            if in_path:
                nearest          = in_path[0]
                htf_liq_dist_atr = round(abs(nearest["price"] - price) / atr_h4, 2)
                sweep_done = detect_liquidity_sweep(
                    df_h4, struct_h4["swing_highs"], struct_h4["swing_lows"],
                    direction, lookback=6,
                    levels=[p["price"] for p in in_path],
                )
                if not sweep_done["swept"]:
                    return self._discard(
                        symbol + "_sw",
                        f"[SWING] Veto HTF: {direction} contra {nearest['kind']} "
                        f"{nearest['tf']} intacto en {nearest['price']:.2f} "
                        f"({htf_liq_dist_atr:.1f} ATR H4)")
        except Exception as e:
            logger.debug(f"HTF veto swing: {e}")

        # ── 5. OB válido en H4 ─────────────────────────────────────
        target_type = "BULLISH" if direction == "BUY" else "BEARISH"
        # Para swing se acepta OB hasta 3x ATR H4 de distancia al precio
        ob_target = self._find_nearest_ob(obs_h4, price, direction, atr_h4 * 3, target_type)
        if ob_target is None:
            return self._discard(
                symbol + "_sw", f"[SWING] Sin OB {target_type} válido en H4")

        # ── 6. Niveles (entry retroceso OB H4, SL y TP proporcionales) ──
        regime_str = regime_data.get("regime", "UNKNOWN")
        entry, sl, tp1, tp2, entry_type = self._calculate_levels(
            direction, price, ob_target, atr_h4, atr_mult,
            struct_h4, liq_h4, regime=regime_str,
        )
        if entry is None:
            return self._discard(symbol + "_sw", "[SWING] Niveles no calculables")

        risk = abs(entry - sl)
        if risk == 0:
            return self._discard(symbol + "_sw", "[SWING] Riesgo cero (entry=SL)")
        rr = abs(tp1 - entry) / risk
        if rr < min_rr:
            return self._discard(
                symbol + "_sw", f"[SWING] R:R {rr:.1f} insuficiente (min {min_rr})")

        # ── 7. Noticias (ventana ±2h para swing) ───────────────────
        news_stat = self.news.is_news_blackout(minutes_buffer=120)
        news_warn = news_stat["reason"] if news_stat["blackout"] else ""
        try:
            news_calendar = self.news.get_daily_summary()
        except Exception:
            news_calendar = []

        # ── 8. Correlaciones ───────────────────────────────────────
        corr_context = None
        dxy_aligned  = False
        pairs_score  = 0.0
        mtf_aligned  = False
        if self.corr and symbol in ("XAUUSD", "XAUEUR"):
            try:
                corr_context = self.corr.get_full_context(symbol, direction)
                dxy_aligned  = corr_context["dxy_aligned"]
                pairs_score  = corr_context["pairs"]["score"]
                mtf_aligned  = corr_context["mtf_aligned"]
            except Exception as e:
                logger.debug(f"Correlaciones swing: {e}")

        # ── 9. Macro ────────────────────────────────────────────────
        macro_data    = {}
        macro_bias    = None
        macro_aligned = False
        if self.macro:
            try:
                macro_data    = self.macro.get_macro_bias()
                macro_bias    = macro_data.get("gold_bias", "NEUTRAL")
                expected_mac  = "BULLISH" if direction == "BUY" else "BEARISH"
                macro_aligned = macro_bias == expected_mac
            except Exception as e:
                logger.debug(f"Macro swing: {e}")

        # ── 10. Confluencias ────────────────────────────────────────
        confluences = 0.0
        cf_details  = []
        cf_details.extend(extra_warnings)

        confluences += 1; cf_details.append(f"Bias D1 {bias}")

        if struct_h4["trend"] != "NEUTRAL":
            h4_ok = (struct_h4["trend"] == "BULLISH") == (direction == "BUY")
            if h4_ok:
                confluences += 1; cf_details.append(f"Estructura H4 confirma {direction}")

        if ob_target["valid"]:
            confluences += 1; cf_details.append(f"OB H4 {ob_target['type']} válido")

        if (direction == "BUY"  and rsi_state == "OVERSOLD") or \
           (direction == "SELL" and rsi_state == "OVERBOUGHT"):
            confluences += 1; cf_details.append(f"RSI H4 {rsi_state}")
        elif rsi_state == "NEUTRAL":
            confluences += 0.5

        if not news_stat["blackout"]:
            confluences += 1; cf_details.append("Sin noticias ±2h")

        if dxy_aligned and corr_context:
            confluences += 1.5; cf_details.append(
                f"DXY alineado {corr_context['dxy']['trend']}")

        if pairs_score >= 0.6:
            confluences += 1;   cf_details.append(f"Pares confirmados ({pairs_score:.0%})")
        elif pairs_score >= 0.4:
            confluences += 0.5

        if mtf_aligned and corr_context:
            mtf_info = corr_context["mtf"]
            confluences += 1; cf_details.append(
                f"Multi-TF {mtf_info.get('bullish_tfs',0)}B/"
                f"{mtf_info.get('bearish_tfs',0)}B")

        if macro_aligned:
            confluences += 1; cf_details.append(f"Macro global {macro_bias}")
        elif macro_bias == "NEUTRAL":
            confluences += 0.3

        vp_score, vp_desc = 0.0, ""
        if self.vp:
            try:
                vp_score, vp_desc = self.vp.get_confluence(
                    price=price, direction=direction, atr=atr_h4, spot_price=price)
                if vp_score > 0:
                    confluences += vp_score; cf_details.append(vp_desc)
            except Exception as e:
                logger.debug(f"VP swing: {e}")

        delta_score, delta_desc = 0.0, ""
        if self.delta:
            try:
                delta_score, delta_desc = self.delta.get_confluence(
                    symbol=symbol, direction=direction, df_h1=df_h4)
                if delta_score != 0:
                    confluences += delta_score
                    if delta_desc: cf_details.append(delta_desc)
            except Exception as e:
                logger.debug(f"Delta swing: {e}")

        tpo_score = self._detect_tpo_weakness(df_h4, direction)
        if tpo_score > 0:
            confluences += tpo_score
            cf_details.append(f"TPO poor {'high' if direction == 'SELL' else 'low'} H4")

        regime_bonus = 0.0
        if regime_str == "TRENDING_UP" and direction == "BUY":
            regime_bonus = 1.0; cf_details.append(
                f"Régimen TRENDING_UP confirma BUY (ADX {regime_data.get('adx',0):.0f})")
        elif regime_str == "TRENDING_DOWN" and direction == "SELL":
            regime_bonus = 1.0; cf_details.append(
                f"Régimen TRENDING_DOWN confirma SELL (ADX {regime_data.get('adx',0):.0f})")
        elif regime_str == "RANGING":
            cf_details.append(f"Régimen RANGING — umbral subido")
        confluences += regime_bonus

        sweep_score = 0.0
        sweep = detect_liquidity_sweep(
            df_h4, struct_h4["swing_highs"], struct_h4["swing_lows"], direction)
        if sweep["swept"]:
            sweep_score = 1.0; confluences += sweep_score
            cf_details.append(f"Liquidity sweep H4 en {sweep['level']:.2f} "
                              f"(hace {sweep['bars_ago']} velas)")

        fvg_score = 0.0
        try:
            for gap in find_fair_value_gaps(df_h4, n_recent=40):
                if not gap["valid"] or gap["type"] != target_type: continue
                if gap["bottom"] <= entry <= gap["top"] or \
                   abs(entry - gap["midpoint"]) <= atr_h4 * 0.5:
                    fvg_score = 0.5; confluences += fvg_score
                    cf_details.append(
                        f"FVG H4 {gap['type']} {gap['bottom']:.2f}-{gap['top']:.2f}")
                    break
        except Exception as e:
            logger.debug(f"FVG swing: {e}")

        h1_aligned = 0
        if not df_h1.empty:
            try:
                struct_h1_sw = detect_market_structure(df_h1, lookback=5)
                ev_h1        = struct_h1_sw.get("last_event")
                if struct_h1_sw["trend"] == bias or \
                   (ev_h1 and ev_h1["direction"] == bias):
                    h1_aligned = 1; confluences += 0.5
                    cf_details.append(f"H1 trigger confirma ({struct_h1_sw['trend']})")
            except Exception as e:
                logger.debug(f"H1 trigger swing: {e}")

        confidence = min(confluences / MAX_CONFLUENCES, 1.0)

        # ── ML ─────────────────────────────────────────────────────
        ml_proba = 0.5
        signal_preview_sw = {
            "direction":     direction,
            "confluences":   int(confluences),
            "confidence":    confidence,
            "rsi_state":     rsi_state,
            "bias_h4":       bias,
            "ob_type":       ob_target["type"],
            "news_blackout": int(news_stat["blackout"]),
            "hour_utc":      datetime.now(timezone.utc).hour,
            "day_of_week":   datetime.now(timezone.utc).weekday(),
            "vp_score":      float(vp_score),
            "delta_score":   float(delta_score),
            "atr_pct":       round(atr_h4 / price * 100, 3) if price > 0 else 0,
            "hurst":         float(regime_data.get("hurst", 0.5)),
            "adx":           float(regime_data.get("adx", 20)),
            "pairs_score":   float(pairs_score),
            "sweep_score":   float(sweep_score),
            "fvg_score":     float(fvg_score),
            "m15_aligned":   int(h1_aligned),
            "htf_liq_dist":  float(htf_liq_dist_atr),
            "timestamp":     datetime.now(timezone.utc).isoformat(),
        }
        if self.ml:
            try:
                ml_proba = self.ml.predict_win_probability(signal_preview_sw)
                if ml_proba >= 0.60:
                    confluences += 1; cf_details.append(f"ML ensemble confirma ({ml_proba:.0%})")
                    confidence = min(confluences / MAX_CONFLUENCES, 1.0)
                veto_thr = float(risk_cfg.get("ml_veto_threshold", 0.40))
                if getattr(self.ml, "last_count", 0) >= 40 and ml_proba < veto_thr:
                    return self._discard(
                        symbol + "_sw",
                        f"[SWING] ML veto: {ml_proba:.0%} < {veto_thr:.0%}")
            except Exception as e:
                logger.debug(f"ML swing: {e}")

        confidence = min(confluences / MAX_CONFLUENCES, 1.0)

        if confluences < min_confluences:
            return self._discard(
                symbol + "_sw",
                f"[SWING] {confluences:.1f} confluencias < mínimo {min_confluences}")

        if self._is_swing_duplicate(symbol, direction, entry, atr_h4, ob=ob_target):
            return self._discard(
                symbol + "_sw",
                f"[SWING] Duplicado: misma idea {direction} (H4 OB sin cambio)")

        # ── Reasoning ──────────────────────────────────────────────
        from analysis.reasoning_engine import generate_reasoning, generate_news_context
        try:
            reasoning = generate_reasoning(signal_preview_sw | {
                "ob_type":           ob_target["type"],
                "rsi_value":         rsi_val,
                "rr":                rr,
                "confluence_details": cf_details,
            }, macro_data, regime_data)
        except Exception:
            reasoning = ""
        try:
            news_context_lines = generate_news_context(news_calendar)
        except Exception:
            news_context_lines = ""

        hold_est = swing_cfg.get("hold_estimate", "2-7 días")

        signal = {
            "symbol":             symbol,
            "direction":          direction,
            "entry":              round(entry, 5),
            "sl":                 round(sl, 5),
            "tp1":                round(tp1, 5),
            "tp2":                round(tp2, 5) if tp2 else None,
            "rr":                 round(rr, 2),
            "confidence":         round(confidence, 2),
            "confluences":        round(confluences, 1),
            "confluence_details": cf_details,
            "timeframe":          "H4",
            "mode":               "SWING",
            "hold_estimate":      hold_est,
            "bias_h4":            bias,
            "structure_h1":       struct_h4["trend"],
            "ob_type":            ob_target["type"],
            "rsi_state":          rsi_state,
            "rsi_value":          round(rsi_val, 1) if rsi_val else None,
            "news_warning":       news_warn,
            "news_blackout":      news_stat["blackout"],
            "news_context":       news_context_lines,
            "dxy_aligned":        dxy_aligned,
            "pairs_score":        round(pairs_score, 2),
            "mtf_aligned":        mtf_aligned,
            "macro_bias":         macro_bias,
            "ml_proba":           round(ml_proba, 2),
            "regime":             regime_str,
            "regime_adx":         float(regime_data.get("adx", 20)),
            "regime_hurst":       float(regime_data.get("hurst", 0.5)),
            "vp_score":           float(vp_score),
            "delta_score":        float(delta_score),
            "sweep_score":        float(sweep_score),
            "fvg_score":          float(fvg_score),
            "m15_aligned":        int(h1_aligned),
            "htf_liq_dist":       float(htf_liq_dist_atr),
            "ob_midpoint":        float(ob_target["midpoint"]),
            "atr_pct":            round(atr_h4 / price * 100, 3) if price > 0 else 0,
            "entry_type":         entry_type,
            "reasoning":          reasoning,
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "atr":                round(atr_h4, 5),
        }

        self._last_swing_signals[symbol] = signal
        logger.info(
            f"[{symbol}] SWING {direction} | Entry:{entry:.5f} SL:{sl:.5f} "
            f"TP1:{tp1:.5f} | R:R:{rr:.1f} | Conf:{confidence:.0%} "
            f"({confluences:.1f}/{MAX_CONFLUENCES}) | ML:{ml_proba:.0%} | "
            f"Hold:{hold_est}"
        )
        return signal

    def _is_swing_duplicate(self, symbol, direction, entry, atr: float = 0.0,
                            ob: dict | None = None):
        """
        Anti-duplicado para swing: misma dirección + mismo OB H4 O entry
        sin moverse > 1.5 ATR H4, dentro del cooldown de 24h configurado.
        Un cambio de dirección NUNCA se bloquea.
        """
        last = self._last_swing_signals.get(symbol)
        if not last or last.get("direction") != direction:
            return False

        cooldown_h = float(self.config.get("swing", {}).get("cooldown_hours", 24))
        try:
            last_t = datetime.fromisoformat(last["timestamp"])
            age_h  = (datetime.now(timezone.utc) - last_t).total_seconds() / 3600
            if age_h >= cooldown_h:
                return False  # Fuera del cooldown → señal nueva aunque sea similar
        except Exception:
            return False

        if not atr or atr <= 0:
            return True  # Dentro del cooldown sin ATR → conservador

        same_entry  = abs(float(entry) - float(last.get("entry", 0))) < atr * 1.5
        last_ob_mid = last.get("ob_midpoint")
        same_ob     = (
            ob is not None and last_ob_mid is not None
            and abs(float(ob.get("midpoint", 0)) - float(last_ob_mid)) < atr * 0.5
        )
        if same_entry or same_ob:
            why = "mismo OB H4" if same_ob else "entry sin cambio (<1.5 ATR H4)"
            logger.info(
                f"[{symbol}] SWING {direction} duplicado por contexto ({why}) "
                f"— {age_h:.1f}h desde el último (cooldown {cooldown_h:.0f}h)"
            )
            return True
        return False

    def _is_duplicate(self, symbol, direction, entry, atr: float = 0.0,
                      ob: dict | None = None):
        """
        Anti-duplicado POR CONTEXTO (señales infinitas, sin cooldown de
        tiempo): una señal de la misma dirección solo es duplicada si es
        LA MISMA IDEA que la anterior —
          - apunta al mismo Order Block (midpoint a < 0.25 ATR), O
          - el entry está a < 0.75 ATR del anterior.
        Si el precio se movió o el setup cambió de OB → señal nueva válida.
        Un cambio de dirección (reversión) NUNCA se bloquea.

        Modo legacy: si risk.signal_cooldown_minutes > 0, se aplica ADEMÁS
        el cooldown por tiempo (comportamiento pre-2026-06-12).

        Nota 11-jun: las 3 SELL apiladas (4096/4085/4081) hoy las frenan
        los vetos de liquidez (displacement + HTF), no el cooldown.
        """
        last = self._last_signals.get(symbol)
        if not last or last.get("direction") != direction:
            return False

        # Modo legacy por tiempo (config > 0)
        cooldown_min = int(
            self.config.get("risk", {}).get("signal_cooldown_minutes", 0)
        )
        if cooldown_min > 0:
            try:
                last_t = datetime.fromisoformat(last["timestamp"])
                if (datetime.now(timezone.utc) - last_t) < timedelta(minutes=cooldown_min):
                    logger.info(
                        f"[{symbol}] Señal {direction} duplicada — última hace "
                        f"{(datetime.now(timezone.utc) - last_t).total_seconds()/60:.0f} min "
                        f"(cooldown {cooldown_min} min)"
                    )
                    return True
            except Exception:
                pass

        # Anti-duplicado por contexto
        if not atr or atr <= 0:
            return False
        try:
            same_entry = abs(float(entry) - float(last.get("entry", 0))) < atr * 0.75
            last_ob_mid = last.get("ob_midpoint")
            same_ob = (
                ob is not None and last_ob_mid is not None
                and abs(float(ob.get("midpoint", 0)) - float(last_ob_mid)) < atr * 0.25
            )
            if same_entry or same_ob:
                why = "mismo OB" if same_ob else "entry sin cambio (<0.75 ATR)"
                logger.info(
                    f"[{symbol}] Señal {direction} duplicada por contexto "
                    f"({why}) — se reenviará cuando el setup cambie"
                )
                return True
        except Exception:
            pass
        return False

    # ── Daytrading M15 ──────────────────────────────────────────────

    def analyze_daytrade(self, symbol: str) -> dict | None:
        """
        Daytrade M15: bias H1 (EMA + estructura) → Order Blocks M15 →
        entry en retroceso con SL corto (extremo OB M15 ± ATR M15).

        Señales en AMBAS direcciones según el bias intradía (el bias H1
        gira varias veces al día, a diferencia del H4), ilimitadas con
        anti-duplicado por contexto. Pensado para ejecución MANUAL en la
        cuenta personal — NO se auto-ejecuta en demo (alert_only).

        Vetos compartidos con el sistema H1 (aplicados sobre M15):
        sweep-contra, displacement-contra y pool de liquidez HTF intacto.
        """
        dt_cfg = self.config.get("daytrade", {}) or {}
        if not dt_cfg.get("enabled", False):
            return None

        # Filtro de sesión propio (ej. "7-11,13-17" = aperturas Londres+NY);
        # sin hours_utc usa la sesión global. El backtest valida ESTA ventana
        # — fuera de ella no se emiten señales daytrade.
        now_h      = datetime.now(timezone.utc).hour
        hours_spec = str(dt_cfg.get("hours_utc", "")).strip()
        if hours_spec:
            try:
                ranges = [tuple(int(x) for x in r.split("-"))
                          for r in hours_spec.split(",")]
            except Exception:
                ranges = []
            if ranges and not any(lo <= now_h < hi for lo, hi in ranges):
                return None
        else:
            sess = self.config.get("sessions", {}).get("allowed_hours_utc", {})
            if not (int(sess.get("start", 7)) <= now_h < int(sess.get("end", 21))):
                return None

        min_rr          = float(dt_cfg.get("min_rr", 1.5))
        tp2_rr          = float(dt_cfg.get("tp2_rr", 2.5))
        atr_mult        = float(dt_cfg.get("atr_sl_multiplier", 1.0))
        min_confluences = float(dt_cfg.get("min_confluences", 4.0))
        dkey            = symbol + "_dt"
        MAX_DT          = 8.0  # escala propia del modelo daytrade

        # ── 0. Régimen — HIGH_VOL bloquea también el daytrade ──────
        regime_data = {}
        if self.regime:
            try:
                regime_data = self.regime.analyze(symbol)
                if not regime_data.get("trade_allowed", True):
                    return self._discard(
                        dkey, f"[DT] Régimen {regime_data.get('regime')} — "
                              f"volatilidad extrema, bloqueado")
            except Exception as e:
                logger.debug(f"[DT] Regime engine: {e}")

        # ── 1. Datos M15 + H1 + H4 ─────────────────────────────────
        df_m15 = self._get_data(symbol, "M15", 300)
        df_h1  = self._get_data(symbol, "H1", 200)
        if df_m15.empty or df_h1.empty:
            return self._discard(dkey, "[DT] Sin datos M15/H1 de MT5")

        # Analizar UNA vez por vela M15 cerrada (el ciclo corre cada 60s)
        last_bar = str(df_m15["time"].iloc[-1])
        if self._last_dt_bar.get(symbol) == last_bar:
            return None
        self._last_dt_bar[symbol] = last_bar

        # ── 2. Bias intradía H1 (cualquier dirección) ──────────────
        ema_bias_h1 = get_ema_bias(df_h1)
        struct_h1   = detect_market_structure(df_h1, lookback=5)
        bias = ema_bias_h1 if ema_bias_h1 != "NEUTRAL" else struct_h1["trend"]
        if bias == "NEUTRAL":
            return self._discard(dkey, "[DT] Bias H1 NEUTRAL — sin dirección intradía")
        direction   = "BUY" if bias == "BULLISH" else "SELL"
        target_type = "BULLISH" if direction == "BUY" else "BEARISH"

        # ── 3. Precio, ATRs, RSI M15 ───────────────────────────────
        tick = self.mt5.get_current_price(symbol)
        if not tick:
            return self._discard(dkey, "[DT] Sin precio actual de MT5")
        price = float(tick["ask"]) if direction == "BUY" else float(tick["bid"])

        atr_m15 = df_m15["atr"].iloc[-1] if "atr" in df_m15.columns else None
        atr_h1  = df_h1["atr"].iloc[-1]  if "atr" in df_h1.columns  else None
        if atr_m15 is None or pd.isna(atr_m15) or float(atr_m15) == 0:
            return self._discard(dkey, "[DT] ATR M15 no disponible")
        atr_m15 = float(atr_m15)
        atr_h1  = float(atr_h1) if atr_h1 is not None and not pd.isna(atr_h1) else atr_m15 * 4

        rsi_val = float(df_m15["rsi"].iloc[-1]) if "rsi" in df_m15.columns else 50.0
        if (direction == "BUY" and rsi_val > 70) or (direction == "SELL" and rsi_val < 30):
            return self._discard(
                dkey, f"[DT] RSI M15 {rsi_val:.0f} extremo contra {direction}")

        # Spread: en daytrading el coste pesa más (SL corto) — máx 15% del SL estimado
        spread = float(tick.get("spread", 0) or 0)
        est_sl = atr_m15 * (atr_mult + 0.5)
        if spread > 0 and est_sl > 0 and spread > est_sl * 0.15:
            return self._discard(
                dkey, f"[DT] Spread {spread:.2f} > 15% del SL estimado ({est_sl:.2f})")

        # ── 4. Vetos de liquidez (mismos que H1, aplicados a M15) ──
        liq_cfg    = self.config.get("liquidity", {}) or {}
        struct_m15 = detect_market_structure(df_m15, lookback=5)

        if liq_cfg.get("sweep_against_veto", False):
            opposite   = "SELL" if direction == "BUY" else "BUY"
            sig_levels = get_significant_levels(df_m15)
            lvls = sig_levels["lows"] if opposite == "BUY" else sig_levels["highs"]
            if lvls:
                sweep_against = detect_liquidity_sweep(
                    df_m15, struct_m15["swing_highs"], struct_m15["swing_lows"],
                    opposite, lookback=int(liq_cfg.get("sweep_against_lookback", 6)),
                    levels=lvls,
                )
                if sweep_against["swept"]:
                    return self._discard(
                        dkey, f"[DT] Veto: barrido contra {direction} en "
                              f"{sweep_against['level']:.2f}")

        if liq_cfg.get("displacement_filter", False):
            disp = detect_displacement(
                df_m15, atr_m15,
                n_candles=int(liq_cfg.get("displacement_candles", 3)),
                body_atr=float(liq_cfg.get("displacement_body_atr", 0.8)),
                net_atr=float(liq_cfg.get("displacement_net_atr", 1.5)),
                exclude_last=True,
            )
            if (disp["direction"] == "BULLISH" and direction == "SELL") or \
               (disp["direction"] == "BEARISH" and direction == "BUY"):
                return self._discard(
                    dkey, f"[DT] Veto: impulso {disp['direction']} M15 contra {direction}")

        htf_liq_dist_atr = 99.0
        if liq_cfg.get("htf_liquidity_veto", True):
            try:
                df_h4 = self._get_data(symbol, "H4", 120)
            except Exception:
                df_h4 = pd.DataFrame()
            pools = find_htf_liquidity_pools(
                [(df_h1, "H1"), (df_h4, "H4")], price, atr_h1,
                max_dist_atr=1.0,  # más cerca que en H1: horizonte intradía
                swing_lookback=int(liq_cfg.get("htf_swing_lookback", 3)),
            )
            in_path = pools["above"] if direction == "SELL" else pools["below"]
            if in_path:
                nearest = in_path[0]
                htf_liq_dist_atr = round(abs(nearest["price"] - price) / atr_h1, 2)
                sweep_done = detect_liquidity_sweep(
                    df_m15, struct_m15["swing_highs"], struct_m15["swing_lows"],
                    direction,
                    lookback=int(liq_cfg.get("sweep_against_lookback", 6)),
                    levels=[p["price"] for p in in_path],
                )
                if not sweep_done["swept"]:
                    return self._discard(
                        dkey, f"[DT] Veto HTF: {direction} contra pool "
                              f"{nearest['kind']} {nearest['tf']} intacto en "
                              f"{nearest['price']:.2f}")
        else:
            df_h4 = pd.DataFrame()

        # ── 5. Order Block M15 ─────────────────────────────────────
        obs_m15 = find_order_blocks(df_m15, n_recent=40)
        ob = self._find_nearest_ob(obs_m15, price, direction, atr_m15, target_type)
        if ob is None:
            return self._discard(
                dkey, f"[DT] Sin Order Block {target_type} M15 cerca del precio")

        # ── 6. Niveles: entry retroceso al OB, SL corto M15 ────────
        inside_ob = ob["bottom"] <= price <= ob["top"]
        if direction == "BUY":
            entry      = price if inside_ob else float(ob["top"])
            sl         = float(ob["bottom"]) - atr_m15 * atr_mult
            entry_type = "MARKET" if inside_ob else "RETRACEMENT"
            risk       = entry - sl
            if risk <= 0:
                return self._discard(dkey, "[DT] Riesgo cero")
            tp1 = entry + risk * min_rr
            tp2 = entry + risk * tp2_rr
        else:
            entry      = price if inside_ob else float(ob["bottom"])
            sl         = float(ob["top"]) + atr_m15 * atr_mult
            entry_type = "MARKET" if inside_ob else "RETRACEMENT"
            risk       = sl - entry
            if risk <= 0:
                return self._discard(dkey, "[DT] Riesgo cero")
            tp1 = entry - risk * min_rr
            tp2 = entry - risk * tp2_rr

        # ── 7. Noticias ────────────────────────────────────────────
        avoid_min = self.config.get("sessions", {}).get("avoid_news_minutes", 30)
        try:
            news_stat = self.news.is_news_blackout(minutes_buffer=avoid_min)
        except Exception:
            news_stat = {"blackout": False, "reason": ""}
        news_warn = news_stat["reason"] if news_stat["blackout"] else ""

        # ── 8. Confluencias (escala propia, máx 8.0) ───────────────
        confluences = 0.0
        cf_details  = []

        confluences += 1.5; cf_details.append(f"Bias H1 {bias}")

        # H4 también alineado (multi-TF)
        try:
            if not df_h4.empty:
                h4_bias = get_ema_bias(df_h4)
                if h4_bias == bias:
                    confluences += 1.0; cf_details.append(f"H4 alineado ({h4_bias})")
        except Exception:
            pass

        # Estructura M15 alineada (BOS/CHoCH a favor)
        ev_m15 = struct_m15.get("last_event")
        if struct_m15["trend"] == bias or (ev_m15 and ev_m15["direction"] == bias):
            confluences += 1.0
            cf_details.append(f"Estructura M15 {struct_m15['trend']}")

        # RSI M15
        if (direction == "BUY" and rsi_val < 40) or \
           (direction == "SELL" and rsi_val > 60):
            confluences += 0.5; cf_details.append(f"RSI M15 {rsi_val:.0f} a favor")
        elif 40 <= rsi_val <= 60:
            confluences += 0.25

        if not news_stat["blackout"]:
            confluences += 0.5; cf_details.append("Sin noticias")

        # Volume Profile COMEX (mismo motor que H1) — SOLO oro: el perfil es de
        # los futuros de oro COMEX (GC=F); no aporta nada a forex/índices.
        vp_score = 0.0
        if self.vp and self._is_gold(symbol):
            try:
                vp_score, vp_desc = self.vp.get_confluence(
                    price=price, direction=direction, atr=atr_m15, spot_price=price
                )
                vp_score = min(vp_score, 1.0)
                if vp_score > 0:
                    confluences += vp_score
                    cf_details.append(vp_desc)
            except Exception as e:
                logger.debug(f"[DT] VP: {e}")

        # Delta ticks (volumen real del propio símbolo — válido para cualquier par)
        delta_score = 0.0
        if self.delta:
            try:
                delta_score, delta_desc = self.delta.get_confluence(
                    symbol=symbol, direction=direction, df_h1=df_m15
                )
                delta_score = max(-0.5, min(delta_score, 0.5))
                if delta_score != 0:
                    confluences += delta_score
                    if delta_desc:
                        cf_details.append(delta_desc)
            except Exception as e:
                logger.debug(f"[DT] Delta: {e}")

        # Sweep a favor en M15 (stop hunt + reclaim)
        sweep_score = 0.0
        try:
            sweep = detect_liquidity_sweep(
                df_m15, struct_m15["swing_highs"], struct_m15["swing_lows"], direction
            )
            if sweep["swept"]:
                sweep_score = 1.0
                confluences += sweep_score
                cf_details.append(f"Sweep M15 en {sweep['level']:.2f}")
        except Exception:
            pass

        # FVG M15 alineado con el entry
        fvg_score = 0.0
        try:
            for gap in find_fair_value_gaps(df_m15, n_recent=40):
                if not gap["valid"] or gap["type"] != target_type:
                    continue
                if gap["bottom"] <= entry <= gap["top"] or \
                   abs(entry - gap["midpoint"]) <= atr_m15 * 0.5:
                    fvg_score = 0.5
                    confluences += fvg_score
                    cf_details.append(f"FVG M15 {gap['bottom']:.2f}-{gap['top']:.2f}")
                    break
        except Exception:
            pass

        # Régimen trending a favor
        regime_str = regime_data.get("regime", "UNKNOWN")
        if (regime_str == "TRENDING_UP" and direction == "BUY") or \
           (regime_str == "TRENDING_DOWN" and direction == "SELL"):
            confluences += 0.5
            cf_details.append(f"Régimen {regime_str}")

        confidence = min(confluences / MAX_DT, 1.0)

        if confluences < min_confluences:
            return self._discard(
                dkey, f"[DT] {confluences:.1f} confluencias < mínimo {min_confluences}")

        if self._is_daytrade_duplicate(symbol, direction, entry, atr_m15, ob=ob):
            return self._discard(
                dkey, f"[DT] Duplicada: misma idea {direction} ya enviada")

        # ── 9. Señal ───────────────────────────────────────────────
        signal = {
            "symbol":             symbol,
            "direction":          direction,
            "entry":              round(entry, 5),
            "sl":                 round(sl, 5),
            "tp1":                round(tp1, 5),
            "tp2":                round(tp2, 5),
            "rr":                 round(min_rr, 2),
            "confidence":         round(confidence, 2),
            "confluences":        round(confluences, 1),
            "confluence_details": cf_details,
            "timeframe":          "M15",
            "mode":               "DAYTRADE",
            "model":              "DAYTRADE",
            "max_confluences":    MAX_DT,
            "bias_h4":            f"{bias} (H1)",
            "structure_h1":       struct_m15["trend"],
            "ob_type":            ob["type"],
            "ob_midpoint":        float(ob["midpoint"]),
            "rsi_state":          "OVERBOUGHT" if rsi_val > 70 else
                                  ("OVERSOLD" if rsi_val < 30 else "NEUTRAL"),
            "rsi_value":          round(rsi_val, 1),
            "news_warning":       news_warn,
            "news_blackout":      news_stat["blackout"],
            "regime":             regime_str,
            "regime_adx":         float(regime_data.get("adx", 20)),
            "regime_hurst":       float(regime_data.get("hurst", 0.5)),
            "vp_score":           float(vp_score),
            "delta_score":        float(delta_score),
            "sweep_score":        float(sweep_score),
            "fvg_score":          float(fvg_score),
            "m15_aligned":        1,
            "htf_liq_dist":       float(htf_liq_dist_atr),
            "ml_proba":           0.5,   # ML entrenado en features H1 — no aplica
            "atr":                round(atr_m15, 5),
            "atr_pct":            round(atr_m15 / price * 100, 3) if price > 0 else 0,
            "entry_type":         entry_type,
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            # Niveles nativos M15 → los bloques 2K y MI CUENTA los usan directo
            "funded_levels":      {"entry": round(entry, 2), "sl": round(sl, 2),
                                   "refined": True},
        }
        if dt_cfg.get("alert_only", True):
            signal["no_auto_execute"] = True

        self._last_dt_signals[symbol] = signal
        logger.info(
            f"[DT] [{symbol}] SEÑAL {direction} M15 | Entry:{entry:.2f} SL:{sl:.2f} "
            f"TP1:{tp1:.2f} | R:R:{min_rr:.1f} | Conf:{confidence:.0%} "
            f"({confluences:.1f}/{MAX_DT})"
        )
        return signal

    def analyze_scalp(self, symbol: str) -> dict | None:
        """
        Scalp M5: bias M15 (EMA + estructura) → Order Blocks M5 → entry en
        retroceso con SL muy corto (extremo OB M5 ± ATR M5). Versión más rápida
        del daytrade, pensada para forex/índices de tick fino (y oro).

        Señales en AMBAS direcciones según el bias M15, ilimitadas con
        anti-duplicado por contexto. Ejecución MANUAL — NUNCA se auto-ejecuta.
        Reusa los mismos vetos de liquidez del daytrade aplicados sobre M5.
        """
        sc_cfg = self.config.get("scalp", {}) or {}
        if not sc_cfg.get("enabled", False):
            return None

        # Filtro de sesión propio (ej. "7-11,13-17"); sin hours_utc usa la global.
        now_h      = datetime.now(timezone.utc).hour
        hours_spec = str(sc_cfg.get("hours_utc", "")).strip()
        if hours_spec:
            try:
                ranges = [tuple(int(x) for x in r.split("-"))
                          for r in hours_spec.split(",")]
            except Exception:
                ranges = []
            if ranges and not any(lo <= now_h < hi for lo, hi in ranges):
                return None
        else:
            sess = self.config.get("sessions", {}).get("allowed_hours_utc", {})
            if not (int(sess.get("start", 7)) <= now_h < int(sess.get("end", 21))):
                return None

        min_rr          = float(sc_cfg.get("min_rr", 1.5))
        tp2_rr          = float(sc_cfg.get("tp2_rr", 2.5))
        atr_mult        = float(sc_cfg.get("atr_sl_multiplier", 1.2))
        min_confluences = float(sc_cfg.get("min_confluences", 3.5))
        spread_sl_pct   = float(sc_cfg.get("max_spread_sl_pct", 0.20))
        dkey            = symbol + "_sc"
        MAX_SC          = 6.5  # escala propia del modelo scalp

        # ── 0. Régimen — HIGH_VOL bloquea también el scalp ─────────
        regime_data = {}
        if self.regime:
            try:
                regime_data = self.regime.analyze(symbol)
                if not regime_data.get("trade_allowed", True):
                    return self._discard(
                        dkey, f"[SC] Régimen {regime_data.get('regime')} — "
                              f"volatilidad extrema, bloqueado")
            except Exception as e:
                logger.debug(f"[SC] Regime engine: {e}")

        # ── 1. Datos M5 + M15 (bias) + H1 (contexto HTF) ───────────
        df_m5  = self._get_data(symbol, "M5", 300)
        df_m15 = self._get_data(symbol, "M15", 200)
        if df_m5.empty or df_m15.empty:
            return self._discard(dkey, "[SC] Sin datos M5/M15 de MT5")

        # Analizar UNA vez por vela M5 cerrada (el ciclo corre cada 60s)
        last_bar = str(df_m5["time"].iloc[-1])
        if self._last_scalp_bar.get(symbol) == last_bar:
            return None
        self._last_scalp_bar[symbol] = last_bar

        # ── 2. Bias intradía M15 (cualquier dirección) ─────────────
        ema_bias_m15 = get_ema_bias(df_m15)
        struct_m15b  = detect_market_structure(df_m15, lookback=5)
        bias = ema_bias_m15 if ema_bias_m15 != "NEUTRAL" else struct_m15b["trend"]
        if bias == "NEUTRAL":
            return self._discard(dkey, "[SC] Bias M15 NEUTRAL — sin dirección")
        direction   = "BUY" if bias == "BULLISH" else "SELL"
        target_type = "BULLISH" if direction == "BUY" else "BEARISH"

        # ── 3. Precio, ATRs, RSI M5 ────────────────────────────────
        tick = self.mt5.get_current_price(symbol)
        if not tick:
            return self._discard(dkey, "[SC] Sin precio actual de MT5")
        price = float(tick["ask"]) if direction == "BUY" else float(tick["bid"])

        atr_m5  = df_m5["atr"].iloc[-1]  if "atr" in df_m5.columns  else None
        atr_m15 = df_m15["atr"].iloc[-1] if "atr" in df_m15.columns else None
        if atr_m5 is None or pd.isna(atr_m5) or float(atr_m5) == 0:
            return self._discard(dkey, "[SC] ATR M5 no disponible")
        atr_m5  = float(atr_m5)
        atr_m15 = float(atr_m15) if atr_m15 is not None and not pd.isna(atr_m15) else atr_m5 * 3

        rsi_val = float(df_m5["rsi"].iloc[-1]) if "rsi" in df_m5.columns else 50.0
        if (direction == "BUY" and rsi_val > 75) or (direction == "SELL" and rsi_val < 25):
            return self._discard(
                dkey, f"[SC] RSI M5 {rsi_val:.0f} extremo contra {direction}")

        # Spread: en scalp el coste pesa MUCHO (SL muy corto)
        spread = float(tick.get("spread", 0) or 0)
        est_sl = atr_m5 * (atr_mult + 0.5)
        if spread > 0 and est_sl > 0 and spread > est_sl * spread_sl_pct:
            return self._discard(
                dkey, f"[SC] Spread {spread:.5f} > {spread_sl_pct:.0%} del SL "
                      f"estimado ({est_sl:.5f}) — coste se come el edge")

        # ── 4. Vetos de liquidez (mismos que daytrade, sobre M5) ───
        liq_cfg   = self.config.get("liquidity", {}) or {}
        struct_m5 = detect_market_structure(df_m5, lookback=5)

        if liq_cfg.get("sweep_against_veto", False):
            opposite   = "SELL" if direction == "BUY" else "BUY"
            sig_levels = get_significant_levels(df_m5)
            lvls = sig_levels["lows"] if opposite == "BUY" else sig_levels["highs"]
            if lvls:
                sweep_against = detect_liquidity_sweep(
                    df_m5, struct_m5["swing_highs"], struct_m5["swing_lows"],
                    opposite, lookback=int(liq_cfg.get("sweep_against_lookback", 6)),
                    levels=lvls,
                )
                if sweep_against["swept"]:
                    return self._discard(
                        dkey, f"[SC] Veto: barrido contra {direction} en "
                              f"{sweep_against['level']:.5f}")

        if liq_cfg.get("displacement_filter", False):
            disp = detect_displacement(
                df_m5, atr_m5,
                n_candles=int(liq_cfg.get("displacement_candles", 3)),
                body_atr=float(liq_cfg.get("displacement_body_atr", 0.8)),
                net_atr=float(liq_cfg.get("displacement_net_atr", 1.5)),
                exclude_last=True,
            )
            if (disp["direction"] == "BULLISH" and direction == "SELL") or \
               (disp["direction"] == "BEARISH" and direction == "BUY"):
                return self._discard(
                    dkey, f"[SC] Veto: impulso {disp['direction']} M5 contra {direction}")

        # ── 5. Order Block M5 ──────────────────────────────────────
        obs_m5 = find_order_blocks(df_m5, n_recent=40)
        ob = self._find_nearest_ob(obs_m5, price, direction, atr_m5, target_type)
        if ob is None:
            return self._discard(
                dkey, f"[SC] Sin Order Block {target_type} M5 cerca del precio")

        # ── 6. Niveles: entry retroceso al OB, SL muy corto M5 ─────
        inside_ob = ob["bottom"] <= price <= ob["top"]
        if direction == "BUY":
            entry      = price if inside_ob else float(ob["top"])
            sl         = float(ob["bottom"]) - atr_m5 * atr_mult
            entry_type = "MARKET" if inside_ob else "RETRACEMENT"
            risk       = entry - sl
            if risk <= 0:
                return self._discard(dkey, "[SC] Riesgo cero")
            tp1 = entry + risk * min_rr
            tp2 = entry + risk * tp2_rr
        else:
            entry      = price if inside_ob else float(ob["bottom"])
            sl         = float(ob["top"]) + atr_m5 * atr_mult
            entry_type = "MARKET" if inside_ob else "RETRACEMENT"
            risk       = sl - entry
            if risk <= 0:
                return self._discard(dkey, "[SC] Riesgo cero")
            tp1 = entry - risk * min_rr
            tp2 = entry - risk * tp2_rr

        # ── 7. Noticias ────────────────────────────────────────────
        avoid_min = self.config.get("sessions", {}).get("avoid_news_minutes", 30)
        try:
            news_stat = self.news.is_news_blackout(minutes_buffer=avoid_min)
        except Exception:
            news_stat = {"blackout": False, "reason": ""}
        news_warn = news_stat["reason"] if news_stat["blackout"] else ""

        # ── 8. Confluencias (escala propia, máx 6.5) ───────────────
        confluences = 0.0
        cf_details  = []

        confluences += 1.5; cf_details.append(f"Bias M15 {bias}")

        # Estructura M5 alineada (BOS/CHoCH a favor)
        ev_m5 = struct_m5.get("last_event")
        if struct_m5["trend"] == bias or (ev_m5 and ev_m5["direction"] == bias):
            confluences += 1.0
            cf_details.append(f"Estructura M5 {struct_m5['trend']}")

        # RSI M5
        if (direction == "BUY" and rsi_val < 40) or \
           (direction == "SELL" and rsi_val > 60):
            confluences += 0.5; cf_details.append(f"RSI M5 {rsi_val:.0f} a favor")
        elif 40 <= rsi_val <= 60:
            confluences += 0.25

        if not news_stat["blackout"]:
            confluences += 0.5; cf_details.append("Sin noticias")

        # Volume Profile COMEX — SOLO oro (perfil de GC=F)
        vp_score = 0.0
        if self.vp and self._is_gold(symbol):
            try:
                vp_score, vp_desc = self.vp.get_confluence(
                    price=price, direction=direction, atr=atr_m5, spot_price=price
                )
                vp_score = min(vp_score, 1.0)
                if vp_score > 0:
                    confluences += vp_score
                    cf_details.append(vp_desc)
            except Exception as e:
                logger.debug(f"[SC] VP: {e}")

        # Delta ticks (volumen real del propio símbolo — vale para cualquier par)
        delta_score = 0.0
        if self.delta:
            try:
                delta_score, delta_desc = self.delta.get_confluence(
                    symbol=symbol, direction=direction, df_h1=df_m5
                )
                delta_score = max(-0.5, min(delta_score, 0.5))
                if delta_score != 0:
                    confluences += delta_score
                    if delta_desc:
                        cf_details.append(delta_desc)
            except Exception as e:
                logger.debug(f"[SC] Delta: {e}")

        # Sweep a favor en M5 (stop hunt + reclaim)
        sweep_score = 0.0
        try:
            sweep = detect_liquidity_sweep(
                df_m5, struct_m5["swing_highs"], struct_m5["swing_lows"], direction
            )
            if sweep["swept"]:
                sweep_score = 1.0
                confluences += sweep_score
                cf_details.append(f"Sweep M5 en {sweep['level']:.5f}")
        except Exception:
            pass

        # FVG M5 alineado con el entry
        fvg_score = 0.0
        try:
            for gap in find_fair_value_gaps(df_m5, n_recent=40):
                if not gap["valid"] or gap["type"] != target_type:
                    continue
                if gap["bottom"] <= entry <= gap["top"] or \
                   abs(entry - gap["midpoint"]) <= atr_m5 * 0.5:
                    fvg_score = 0.5
                    confluences += fvg_score
                    cf_details.append(f"FVG M5 {gap['bottom']:.5f}-{gap['top']:.5f}")
                    break
        except Exception:
            pass

        # Régimen trending a favor
        regime_str = regime_data.get("regime", "UNKNOWN")
        if (regime_str == "TRENDING_UP" and direction == "BUY") or \
           (regime_str == "TRENDING_DOWN" and direction == "SELL"):
            confluences += 0.5
            cf_details.append(f"Régimen {regime_str}")

        confidence = min(confluences / MAX_SC, 1.0)

        if confluences < min_confluences:
            return self._discard(
                dkey, f"[SC] {confluences:.1f} confluencias < mínimo {min_confluences}")

        if self._is_scalp_duplicate(symbol, direction, entry, atr_m5, ob=ob):
            return self._discard(
                dkey, f"[SC] Duplicada: misma idea {direction} ya enviada")

        # ── 9. Señal ───────────────────────────────────────────────
        signal = {
            "symbol":             symbol,
            "direction":          direction,
            "entry":              round(entry, 5),
            "sl":                 round(sl, 5),
            "tp1":                round(tp1, 5),
            "tp2":                round(tp2, 5),
            "rr":                 round(min_rr, 2),
            "confidence":         round(confidence, 2),
            "confluences":        round(confluences, 1),
            "confluence_details": cf_details,
            "timeframe":          "M5",
            "mode":               "SCALP",
            "model":              "SCALP",
            "max_confluences":    MAX_SC,
            "bias_h4":            f"{bias} (M15)",
            "structure_h1":       struct_m5["trend"],
            "ob_type":            ob["type"],
            "ob_midpoint":        float(ob["midpoint"]),
            "rsi_state":          "OVERBOUGHT" if rsi_val > 70 else
                                  ("OVERSOLD" if rsi_val < 30 else "NEUTRAL"),
            "rsi_value":          round(rsi_val, 1),
            "news_warning":       news_warn,
            "news_blackout":      news_stat["blackout"],
            "regime":             regime_str,
            "regime_adx":         float(regime_data.get("adx", 20)),
            "regime_hurst":       float(regime_data.get("hurst", 0.5)),
            "vp_score":           float(vp_score),
            "delta_score":        float(delta_score),
            "sweep_score":        float(sweep_score),
            "fvg_score":          float(fvg_score),
            "m15_aligned":        1,
            "htf_liq_dist":       99.0,
            "ml_proba":           0.5,   # ML entrenado en features H1 — no aplica
            "atr":                round(atr_m5, 5),
            "atr_pct":            round(atr_m5 / price * 100, 3) if price > 0 else 0,
            "entry_type":         entry_type,
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            # Niveles nativos M5 → los bloques MI CUENTA / 2K los usan directo
            "funded_levels":      {"entry": round(entry, 5), "sl": round(sl, 5),
                                   "refined": True},
        }
        if sc_cfg.get("alert_only", True):
            signal["no_auto_execute"] = True

        self._last_scalp_signals[symbol] = signal
        logger.info(
            f"[SC] [{symbol}] SEÑAL {direction} M5 | Entry:{entry:.5f} SL:{sl:.5f} "
            f"TP1:{tp1:.5f} | R:R:{min_rr:.1f} | Conf:{confidence:.0%} "
            f"({confluences:.1f}/{MAX_SC})"
        )
        return signal

    def _is_scalp_duplicate(self, symbol, direction, entry,
                            atr_m5: float = 0.0, ob: dict | None = None):
        """Anti-duplicado por contexto del scalp (igual lógica que daytrade,
        sobre M5). Cambio de dirección NUNCA se bloquea."""
        last = self._last_scalp_signals.get(symbol)
        if not last or last.get("direction") != direction:
            return False
        if not atr_m5 or atr_m5 <= 0:
            return False
        try:
            same_entry = abs(float(entry) - float(last.get("entry", 0))) < atr_m5 * 0.5
            last_mid   = last.get("ob_midpoint")
            same_ob    = (
                ob is not None and last_mid is not None
                and abs(float(ob.get("midpoint", 0)) - float(last_mid)) < atr_m5 * 0.25
            )
            if same_entry or same_ob:
                logger.info(
                    f"[SC] [{symbol}] {direction} duplicada por contexto "
                    f"({'mismo OB M5' if same_ob else 'entry sin cambio'})"
                )
                return True
        except Exception:
            pass
        return False

    def _is_daytrade_duplicate(self, symbol, direction, entry,
                               atr_m15: float = 0.0, ob: dict | None = None):
        """
        Anti-duplicado por contexto del daytrade (señales ilimitadas):
        misma dirección + mismo OB M15 (midpoint < 0.25 ATR M15) o entry
        a < 0.5 ATR M15 del anterior = misma idea. Cambio de dirección
        NUNCA se bloquea.
        """
        last = self._last_dt_signals.get(symbol)
        if not last or last.get("direction") != direction:
            return False
        if not atr_m15 or atr_m15 <= 0:
            return False
        try:
            same_entry = abs(float(entry) - float(last.get("entry", 0))) < atr_m15 * 0.5
            last_mid   = last.get("ob_midpoint")
            same_ob    = (
                ob is not None and last_mid is not None
                and abs(float(ob.get("midpoint", 0)) - float(last_mid)) < atr_m15 * 0.25
            )
            if same_entry or same_ob:
                logger.info(
                    f"[DT] [{symbol}] {direction} duplicada por contexto "
                    f"({'mismo OB M15' if same_ob else 'entry sin cambio'})"
                )
                return True
        except Exception:
            pass
        return False

"""
Motor de señales — combina SMC, correlaciones, macro, ML ensemble y técnicas avanzadas.

Confluencias posibles (máx 14.5):
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
"""

import pandas as pd
import logging
from datetime import datetime, timezone, timedelta

from analysis.indicators import add_indicators, get_ema_bias, get_rsi_state
from analysis.market_structure import (
    detect_market_structure,
    find_order_blocks,
    find_liquidity_zones,
)

logger = logging.getLogger(__name__)

MAX_CONFLUENCES = 14.5


class SignalEngine:
    def __init__(self, mt5_connector, news_feed, config: dict,
                 correlation_engine=None, macro_feed=None, learning_engine=None,
                 volume_profile=None, delta_engine=None, regime_engine=None):
        self.mt5          = mt5_connector
        self.news         = news_feed
        self.config       = config
        self.corr         = correlation_engine
        self.macro        = macro_feed
        self.ml           = learning_engine
        self.vp           = volume_profile
        self.delta        = delta_engine
        self.regime       = regime_engine      # MarketRegimeEngine (nuevo)
        self._last_signals: dict = {}

    def analyze(self, symbol: str) -> dict | None:
        """
        Análisis completo con hasta 14.5 confluencias.
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
                    return None
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
            return None

        # ── 2. Bias macro H4 ──────────────────────────────────────
        ema_bias  = get_ema_bias(df_h4)
        struct_h4 = detect_market_structure(df_h4, lookback=5)
        trend_h4  = struct_h4["trend"]

        if ema_bias == trend_h4 and ema_bias != "NEUTRAL":
            bias = ema_bias
        elif ema_bias != "NEUTRAL":
            bias = ema_bias
        elif trend_h4 != "NEUTRAL":
            bias = trend_h4
        else:
            return None

        direction = "BUY" if bias == "BULLISH" else "SELL"

        # ── 3. Estructura y OBs en H1 ────────────────────────────
        struct_h1 = detect_market_structure(df_h1, lookback=5)
        obs       = find_order_blocks(df_h1, n_recent=40)
        liq       = find_liquidity_zones(df_h1, struct_h1["swing_highs"], struct_h1["swing_lows"])

        # ── 4. Precio, ATR, RSI ───────────────────────────────────
        tick = self.mt5.get_current_price(symbol)
        if not tick:
            return None

        price = float(tick["ask"]) if direction == "BUY" else float(tick["bid"])
        atr   = df_h1["atr"].iloc[-1] if "atr" in df_h1.columns else None
        if atr is None or pd.isna(atr) or float(atr) == 0:
            return None
        atr       = float(atr)
        rsi_state = get_rsi_state(df_h1)
        rsi_val   = float(df_h1["rsi"].iloc[-1]) if "rsi" in df_h1.columns else None

        # ── 5. Buscar OB válido ───────────────────────────────────
        target_type = "BULLISH" if direction == "BUY" else "BEARISH"
        ob_target   = self._find_nearest_ob(obs, price, direction, atr, target_type)
        if ob_target is None:
            return None

        # ── 6. Niveles de trading ─────────────────────────────────
        entry, sl, tp1, tp2 = self._calculate_levels(
            direction, price, ob_target, atr, atr_mult, struct_h1, liq
        )
        if entry is None:
            return None

        risk = abs(entry - sl)
        if risk == 0:
            return None
        rr = abs(tp1 - entry) / risk
        if rr < min_rr:
            return None

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
            "timestamp":      datetime.now(timezone.utc).isoformat(),
        }
        if self.ml:
            try:
                ml_proba = self.ml.predict_win_probability(signal_preview)
                # Threshold bajado a 0.60 (ensemble es más robusto que RF solo)
                if ml_proba >= 0.60:
                    confluences += 1; cf_details.append(f"ML ensemble confirma ({ml_proba:.0%})")
                confidence = min(confluences / MAX_CONFLUENCES, 1.0)
            except Exception as e:
                logger.debug(f"ML no disponible: {e}")

        # Confidence final
        confidence = min(confluences / MAX_CONFLUENCES, 1.0)

        # Mínimo de confluencias
        if confluences < min_confluences:
            logger.debug(
                f"[{symbol}] {confluences:.1f} confluencias — descartado "
                f"(min {min_confluences}, régimen: {regime_str})"
            )
            return None

        if self._is_duplicate(symbol, direction, entry):
            return None

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
            "reasoning":          reasoning,
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "atr":                round(atr, 5),
        }

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

    def _calculate_levels(self, direction, price, ob, atr, atr_mult, structure, liq):
        entry = price
        if direction == "BUY":
            sl   = ob["bottom"] - (atr * atr_mult)
            risk = abs(entry - sl)
            next_high = structure.get("last_high")
            tp1 = next_high["price"] if next_high and next_high["price"] > entry + risk else entry + risk * 2
            tp2 = next((z["price"] for z in liq.get("buy_side", []) if z["price"] > tp1), entry + risk * 3)
        else:
            sl   = ob["top"] + (atr * atr_mult)
            risk = abs(sl - entry)
            next_low = structure.get("last_low")
            tp1 = next_low["price"] if next_low and next_low["price"] < entry - risk else entry - risk * 2
            tp2 = next((z["price"] for z in liq.get("sell_side", []) if z["price"] < tp1), entry - risk * 3)
        return entry, sl, tp1, tp2

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

    def _is_duplicate(self, symbol, direction, entry):
        last = self._last_signals.get(symbol)
        if not last:
            return False
        if last["direction"] == direction and entry > 0:
            if abs(last["entry"] - entry) / entry < 0.002:
                try:
                    last_t = datetime.fromisoformat(last["timestamp"])
                    if (datetime.now(timezone.utc) - last_t) < timedelta(minutes=30):
                        return True
                except Exception:
                    pass
        return False

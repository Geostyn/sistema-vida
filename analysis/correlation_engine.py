"""
Correlation Engine — Análisis de correlaciones cross-asset para XAUUSD.

Relaciones clave:
  DXY  ↑ → XAUUSD ↓  (correlacion -0.85)
  DXY  ↓ → XAUUSD ↑
  EUR/USD ↑ → XAUUSD ↑  (correlacion +0.70, ambos vs USD)
  USD/JPY ↑ → XAUUSD ↓  (correlacion -0.60)
  VIX  ↑ → XAUUSD ↑  (activo refugio)
  Yields↑ → XAUUSD ↓  (coste de oportunidad)
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

# Pesos del DXY (ICE Dollar Index oficial — cesta completa de 6 divisas)
DXY_WEIGHTS = {
    "EURUSD": -0.576,   # negativo = correlacion inversa con DXY
    "USDJPY": +0.136,
    "GBPUSD": -0.119,
    "USDCAD": +0.091,
    "USDSEK": +0.042,
    "USDCHF": +0.036,
}

# Constante oficial del ICE: DXY = K * prod(par^peso) da el NIVEL real del índice
DXY_CONSTANT = 50.14348112


class CorrelationEngine:
    def __init__(self, mt5_connector):
        self.mt5 = mt5_connector

    def get_dxy_synthetic(self, n_bars: int = 100, timeframe: str = "H1") -> pd.Series | None:
        """
        Calcula el DXY desde los pares de MT5 — TIEMPO REAL, sin yfinance.

        Si están los 6 pares ICE → fórmula geométrica oficial:
          DXY = 50.14348112 × EURUSD^-0.576 × USDJPY^0.136 × GBPUSD^-0.119
                            × USDCAD^0.091 × USDSEK^0.042 × USDCHF^0.036
        → da el NIVEL REAL del índice (ej. 99.42), no un índice base-100.

        Si falta algún par → fallback al índice de retornos ponderados (base 100).
        """
        prices = {}
        for symbol in DXY_WEIGHTS:
            df = self.mt5.get_rates(symbol, timeframe, n_bars)
            if df.empty:
                continue
            prices[symbol] = df.set_index("time")["close"]

        if len(prices) < 2:
            logger.warning("Menos de 2 pares disponibles para DXY sintético")
            return None

        # Alinear series por tiempo
        aligned = pd.DataFrame(prices).dropna()
        if aligned.empty:
            return None

        # ── Cesta completa → nivel real del índice ────────────────
        if len(aligned.columns) == len(DXY_WEIGHTS):
            dxy_index = pd.Series(DXY_CONSTANT, index=aligned.index)
            for symbol, weight in DXY_WEIGHTS.items():
                dxy_index = dxy_index * aligned[symbol].pow(weight)
            return dxy_index

        # ── Fallback: índice de retornos ponderados (base 100) ────
        dxy_log = pd.Series(0.0, index=aligned.index)
        for symbol, weight in DXY_WEIGHTS.items():
            if symbol in aligned.columns:
                log_ret = np.log(aligned[symbol] / aligned[symbol].shift(1))
                dxy_log += weight * log_ret
        return 100 * np.exp(dxy_log.fillna(0).cumsum())

    def get_dxy_realtime(self) -> float | None:
        """
        Valor SPOT del DXY desde los ticks en vivo de MT5 (mid bid/ask).
        Actualizado al segundo — sin retraso ni rate limit.
        """
        try:
            value = DXY_CONSTANT
            for symbol, weight in DXY_WEIGHTS.items():
                tick = self.mt5.get_current_price(symbol)
                if not tick or not tick.get("bid"):
                    return None
                mid = (float(tick["bid"]) + float(tick["ask"])) / 2
                value *= mid ** weight
            return round(value, 3)
        except Exception as e:
            logger.debug(f"DXY realtime error: {e}")
            return None

    def get_dxy_ohlc(self, timeframe: str = "M5", n_bars: int = 288) -> pd.DataFrame:
        """
        Velas OHLC del DXY sintético para el dashboard.
        Open/Close son exactos; High/Low son la mejor aproximación posible
        (los extremos de cada par no coinciden en el mismo segundo).
        Returns: DataFrame con time, open, high, low, close
        """
        data = {}
        for symbol in DXY_WEIGHTS:
            df = self.mt5.get_rates(symbol, timeframe, n_bars)
            if df.empty:
                return pd.DataFrame()
            data[symbol] = df.set_index("time")[["open", "high", "low", "close"]]

        idx = None
        for df in data.values():
            idx = df.index if idx is None else idx.intersection(df.index)
        if idx is None or len(idx) == 0:
            return pd.DataFrame()

        out = pd.DataFrame(index=idx)
        for col in ("open", "close"):
            series = pd.Series(DXY_CONSTANT, index=idx)
            for symbol, weight in DXY_WEIGHTS.items():
                series = series * data[symbol].loc[idx, col].pow(weight)
            out[col] = series

        # High del DXY: extremo favorable al USD en cada par (peso>0 → high, peso<0 → low)
        hi = pd.Series(DXY_CONSTANT, index=idx)
        lo = pd.Series(DXY_CONSTANT, index=idx)
        for symbol, weight in DXY_WEIGHTS.items():
            if weight > 0:
                hi = hi * data[symbol].loc[idx, "high"].pow(weight)
                lo = lo * data[symbol].loc[idx, "low"].pow(weight)
            else:
                hi = hi * data[symbol].loc[idx, "low"].pow(weight)
                lo = lo * data[symbol].loc[idx, "high"].pow(weight)
        out["high"] = hi
        out["low"]  = lo

        out = out[["open", "high", "low", "close"]].reset_index()
        out["ema20"] = out["close"].ewm(span=20, adjust=False).mean()
        return out

    def get_dxy_trend(self, n_bars: int = 50) -> dict:
        """
        Determina la tendencia actual del DXY sintético.
        Returns: {trend, ema_short, ema_long, momentum, score}
        score: -1 (DXY muy bajista = alcista oro) a +1 (DXY muy alcista = bajista oro)
        """
        dxy = self.get_dxy_synthetic(n_bars)
        if dxy is None or len(dxy) < 20:
            return {"trend": "NEUTRAL", "score": 0.0}

        ema8  = float(dxy.ewm(span=8,  adjust=False).mean().iloc[-1])
        ema21 = float(dxy.ewm(span=21, adjust=False).mean().iloc[-1])
        last  = float(dxy.iloc[-1])
        prev6 = float(dxy.iloc[-7]) if len(dxy) >= 7 else float(dxy.iloc[0])
        momentum = (last - prev6) / prev6 * 100

        if ema8 < ema21 and momentum < -0.1:
            trend = "BEARISH"  # DXY bajista → oro alcista
            score = -1.0
        elif ema8 > ema21 and momentum > 0.1:
            trend = "BULLISH"  # DXY alcista → oro bajista
            score = +1.0
        elif abs(momentum) > 0.05:
            trend = "NEUTRAL"
            score = -momentum / abs(momentum) * 0.3  # señal débil
        else:
            trend = "NEUTRAL"
            score = 0.0

        return {
            "trend":    trend,
            "score":    round(score, 2),
            "ema8":     round(ema8, 3),
            "ema21":    round(ema21, 3),
            "momentum": round(momentum, 3),
            "dxy_last": round(last, 3),
        }

    def get_pair_alignment(self, gold_direction: str) -> dict:
        """
        Verifica si los pares correlacionados confirman la dirección del oro.

        gold_direction: "BUY" o "SELL"
        Returns: {score, aligned_pairs, details}
        """
        from analysis.indicators import add_indicators, get_ema_bias

        confirmations = 0
        total = 0
        details = {}

        # EUR/USD correlacion positiva con oro
        # Si oro BUY → EUR/USD también debería ser alcista (ambos vs USD)
        eurusd_df = self.mt5.get_rates("EURUSD", "H4", 200)
        if not eurusd_df.empty:
            eurusd_df = add_indicators(eurusd_df)
            eur_bias = get_ema_bias(eurusd_df)
            expected = "BULLISH" if gold_direction == "BUY" else "BEARISH"
            aligned = eur_bias == expected
            if aligned:
                confirmations += 1
            total += 1
            details["EURUSD"] = {"bias": eur_bias, "alineado": aligned}

        # USD/JPY correlacion inversa con oro
        # Si oro BUY → USD/JPY debería ser bajista (USD debil)
        usdjpy_df = self.mt5.get_rates("USDJPY", "H4", 200)
        if not usdjpy_df.empty:
            usdjpy_df = add_indicators(usdjpy_df)
            jpy_bias = get_ema_bias(usdjpy_df)
            expected_jpy = "BEARISH" if gold_direction == "BUY" else "BULLISH"
            aligned_jpy = jpy_bias == expected_jpy
            if aligned_jpy:
                confirmations += 1
            total += 1
            details["USDJPY"] = {"bias": jpy_bias, "alineado": aligned_jpy}

        # GBP/USD correlacion positiva con oro (moderada)
        gbpusd_df = self.mt5.get_rates("GBPUSD", "H4", 200)
        if not gbpusd_df.empty:
            gbpusd_df = add_indicators(gbpusd_df)
            gbp_bias = get_ema_bias(gbpusd_df)
            expected_gbp = "BULLISH" if gold_direction == "BUY" else "BEARISH"
            aligned_gbp = gbp_bias == expected_gbp
            if aligned_gbp:
                confirmations += 0.7  # peso menor
            total += 1
            details["GBPUSD"] = {"bias": gbp_bias, "alineado": aligned_gbp}

        score = confirmations / total if total > 0 else 0
        return {
            "score":         round(score, 2),
            "confirmations": confirmations,
            "total":         total,
            "details":       details,
        }

    def get_multi_timeframe_bias(self, symbol: str = "XAUUSD") -> dict:
        """
        Analiza el bias del símbolo en múltiples timeframes.
        Returns dict con el alineamiento: cuántos TFs apuntan en la misma dirección.
        """
        from analysis.indicators import add_indicators, get_ema_bias
        from analysis.market_structure import detect_market_structure

        timeframes = {"D1": "D1", "H4": "H4", "H1": "H1", "M15": "M15"}
        biases = {}

        for tf_name, tf in timeframes.items():
            df = self.mt5.get_rates(symbol, tf, 300)
            if df.empty:
                continue
            df = add_indicators(df)
            biases[tf_name] = get_ema_bias(df)

        if not biases:
            return {"consensus": "NEUTRAL", "score": 0.0, "biases": {}}

        counts = {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0}
        for b in biases.values():
            counts[b] = counts.get(b, 0) + 1

        total = len(biases)
        if counts["BULLISH"] > counts["BEARISH"]:
            consensus = "BULLISH"
            score = counts["BULLISH"] / total
        elif counts["BEARISH"] > counts["BULLISH"]:
            consensus = "BEARISH"
            score = counts["BEARISH"] / total
        else:
            consensus = "NEUTRAL"
            score = 0.5

        return {
            "consensus": consensus,
            "score":     round(score, 2),
            "biases":    biases,
            "bullish_tfs": counts["BULLISH"],
            "bearish_tfs": counts["BEARISH"],
        }

    def get_full_context(self, symbol: str, direction: str) -> dict:
        """
        Análisis completo de correlaciones para una señal dada.
        Returns contexto macro completo con score de confluencia.
        """
        dxy     = self.get_dxy_trend()
        pairs   = self.get_pair_alignment(direction)
        mtf     = self.get_multi_timeframe_bias(symbol)

        # Score de correlación para la dirección dada
        # direction=BUY: DXY bearish = positivo, DXY bullish = negativo
        dxy_aligned = False
        dxy_score   = 0.0
        if direction == "BUY":
            dxy_aligned = dxy["trend"] == "BEARISH"
            dxy_score   = max(0, -dxy["score"])
        else:
            dxy_aligned = dxy["trend"] == "BULLISH"
            dxy_score   = max(0, dxy["score"])

        mtf_aligned = mtf["consensus"] == ("BULLISH" if direction == "BUY" else "BEARISH")

        total_score = (
            dxy_score * 0.40 +          # DXY tiene el mayor peso (40%)
            pairs["score"] * 0.35 +     # Pares correlacionados (35%)
            (mtf["score"] if mtf_aligned else 0) * 0.25  # Multi-TF (25%)
        )

        return {
            "dxy":          dxy,
            "pairs":        pairs,
            "mtf":          mtf,
            "dxy_aligned":  dxy_aligned,
            "mtf_aligned":  mtf_aligned,
            "total_score":  round(total_score, 3),
            "strong":       total_score >= 0.5,
        }

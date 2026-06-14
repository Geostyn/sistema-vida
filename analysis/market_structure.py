"""
Estructura de mercado — BOS, CHoCH, Order Blocks y zonas de liquidez.
Basado en Smart Money Concepts (SMC).
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# SWING POINTS
# ──────────────────────────────────────────────────────────────

def find_swing_points(df: pd.DataFrame, lookback: int = 5) -> tuple:
    """
    Encuentra swing highs y lows usando una ventana deslizante.

    Un swing high: el high de esa vela es el mayor de las 'lookback' velas
    a cada lado. Marca un punto donde el precio giró a la baja.

    Un swing low: el low de esa vela es el menor de las 'lookback' velas
    a cada lado. Marca un punto donde el precio giró al alza.

    Returns: (swing_highs, swing_lows) — listas de dicts {index, price, time}
    """
    swing_highs = []
    swing_lows  = []
    highs = df["high"].values
    lows  = df["low"].values
    n = len(df)

    for i in range(lookback, n - lookback):
        window_highs = highs[i - lookback : i + lookback + 1]
        window_lows  = lows[i  - lookback : i + lookback + 1]

        if highs[i] == window_highs.max():
            swing_highs.append({
                "index": i,
                "price": float(highs[i]),
                "time":  df["time"].iloc[i],
            })

        if lows[i] == window_lows.min():
            swing_lows.append({
                "index": i,
                "price": float(lows[i]),
                "time":  df["time"].iloc[i],
            })

    return swing_highs, swing_lows


# ──────────────────────────────────────────────────────────────
# ESTRUCTURA DE MERCADO
# ──────────────────────────────────────────────────────────────

def detect_market_structure(df: pd.DataFrame, lookback: int = 5) -> dict:
    """
    Detecta la tendencia actual y los eventos más recientes de estructura.

    BOS (Break of Structure): el precio rompe en la DIRECCIÓN de la tendencia.
      - Uptrend + precio rompe por encima del último swing high → BOS BULLISH
      - Downtrend + precio rompe por debajo del último swing low → BOS BEARISH

    CHoCH (Change of Character): el precio rompe en CONTRA de la tendencia,
    señalando un posible cambio de dirección.
      - Uptrend + precio cierra por debajo del último higher low → CHoCH BEARISH
      - Downtrend + precio cierra por encima del último lower high → CHoCH BULLISH

    Returns dict con:
      trend      : "BULLISH" | "BEARISH" | "NEUTRAL"
      last_event : {type, direction, level, time} o None
      swing_highs, swing_lows, last_high, last_low
    """
    swing_highs, swing_lows = find_swing_points(df, lookback)

    base = {
        "trend":       "NEUTRAL",
        "last_event":  None,
        "swing_highs": swing_highs,
        "swing_lows":  swing_lows,
        "last_high":   swing_highs[-1] if swing_highs else None,
        "last_low":    swing_lows[-1]  if swing_lows  else None,
    }

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return base

    h1, h2 = swing_highs[-2], swing_highs[-1]  # penúltimo y último swing high
    l1, l2 = swing_lows[-2],  swing_lows[-1]   # penúltimo y último swing low

    hh = h2["price"] > h1["price"]  # Higher High
    hl = l2["price"] > l1["price"]  # Higher Low
    lh = h2["price"] < h1["price"]  # Lower High
    ll = l2["price"] < l1["price"]  # Lower Low

    if hh and hl:
        trend = "BULLISH"
    elif lh and ll:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    current_price = float(df["close"].iloc[-1])
    now = df["time"].iloc[-1]
    last_event = None

    if trend == "BULLISH":
        if current_price > h2["price"]:
            # Precio rompió el último swing high → BOS alcista
            last_event = {"type": "BOS",   "direction": "BULLISH", "level": h2["price"], "time": now}
        elif current_price < l2["price"]:
            # Precio rompió por debajo del último higher low → CHoCH bajista
            last_event = {"type": "CHoCH", "direction": "BEARISH", "level": l2["price"], "time": now}

    elif trend == "BEARISH":
        if current_price < l2["price"]:
            # Precio rompió el último swing low → BOS bajista
            last_event = {"type": "BOS",   "direction": "BEARISH", "level": l2["price"], "time": now}
        elif current_price > h2["price"]:
            # Precio rompió por encima del último lower high → CHoCH alcista
            last_event = {"type": "CHoCH", "direction": "BULLISH", "level": h2["price"], "time": now}

    return {**base, "trend": trend, "last_event": last_event}


# ──────────────────────────────────────────────────────────────
# ORDER BLOCKS
# ──────────────────────────────────────────────────────────────

def find_order_blocks(df: pd.DataFrame, n_recent: int = 40) -> list:
    """
    Detecta Order Blocks (OB) en las últimas n_recent velas.

    Bullish OB: última vela bajista antes de un impulso alcista fuerte.
      → El cuerpo de esa vela bajista es la zona de soporte institucional.

    Bearish OB: última vela alcista antes de un impulso bajista fuerte.
      → El cuerpo de esa vela alcista es la zona de resistencia institucional.

    Un OB es "válido" (still valid) mientras el precio no haya cerrado dentro
    del OB en sentido contrario a su tipo.

    Returns: lista de dicts, ordenados por distancia al precio actual.
    """
    if len(df) < 3:
        return []

    order_blocks  = []
    current_price = float(df["close"].iloc[-1])
    start_idx     = max(1, len(df) - n_recent - 1)

    for i in range(start_idx, len(df) - 1):
        candle = df.iloc[i]
        next_c = df.iloc[i + 1]

        body_curr = abs(float(candle["close"]) - float(candle["open"]))
        body_next = abs(float(next_c["close"]) - float(next_c["open"]))

        # Requiere que el impulso siguiente sea al menos 1.5× el cuerpo actual
        if body_curr == 0 or body_next < body_curr * 1.5:
            continue

        # ─── Bullish OB ───────────────────────────────────────
        if (candle["close"] < candle["open"] and   # vela bajista
                next_c["close"] > next_c["open"]):  # seguida de alcista

            ob_top    = float(candle["open"])
            ob_bottom = float(candle["close"])

            # OB válido si precio sigue por encima del fondo del OB
            valid = current_price > ob_bottom
            dist  = current_price - ob_top

            order_blocks.append({
                "type":         "BULLISH",
                "top":          ob_top,
                "bottom":       ob_bottom,
                "midpoint":     (ob_top + ob_bottom) / 2,
                "time":         candle["time"],
                "index":        i,
                "valid":        valid,
                "distance":     dist,
                "distance_pct": (dist / ob_top) * 100 if ob_top else 0,
            })

        # ─── Bearish OB ───────────────────────────────────────
        elif (candle["close"] > candle["open"] and  # vela alcista
              next_c["close"] < next_c["open"]):    # seguida de bajista

            ob_top    = float(candle["close"])
            ob_bottom = float(candle["open"])

            valid = current_price < ob_top
            dist  = ob_bottom - current_price

            order_blocks.append({
                "type":         "BEARISH",
                "top":          ob_top,
                "bottom":       ob_bottom,
                "midpoint":     (ob_top + ob_bottom) / 2,
                "time":         candle["time"],
                "index":        i,
                "valid":        valid,
                "distance":     dist,
                "distance_pct": (dist / ob_bottom) * 100 if ob_bottom else 0,
            })

    # Ordenar por cercanía al precio
    order_blocks.sort(key=lambda x: abs(x["distance"]))
    return order_blocks


# ──────────────────────────────────────────────────────────────
# FAIR VALUE GAPS (FVG)
# ──────────────────────────────────────────────────────────────

def find_fair_value_gaps(df: pd.DataFrame, n_recent: int = 40) -> list:
    """
    Detecta Fair Value Gaps (imbalances) de 3 velas.

    Bullish FVG: low de la vela 3 > high de la vela 1 → hueco sin negociar
      que actúa como soporte cuando el precio retrocede a llenarlo.
    Bearish FVG: high de la vela 3 < low de la vela 1 → hueco que actúa
      como resistencia.

    Un FVG es "válido" mientras el precio no lo haya llenado por completo.

    Returns: lista de dicts {type, top, bottom, midpoint, time, valid}
    """
    if len(df) < 3:
        return []

    gaps          = []
    current_price = float(df["close"].iloc[-1])
    start_idx     = max(0, len(df) - n_recent - 2)

    for i in range(start_idx, len(df) - 2):
        c1 = df.iloc[i]
        c3 = df.iloc[i + 2]

        # ─── Bullish FVG ──────────────────────────────────────
        if float(c3["low"]) > float(c1["high"]):
            top    = float(c3["low"])
            bottom = float(c1["high"])
            # Lleno si el precio cerró por debajo del fondo del gap
            valid  = current_price > bottom
            gaps.append({
                "type":     "BULLISH",
                "top":      top,
                "bottom":   bottom,
                "midpoint": (top + bottom) / 2,
                "time":     c3["time"],
                "valid":    valid,
            })

        # ─── Bearish FVG ──────────────────────────────────────
        elif float(c3["high"]) < float(c1["low"]):
            top    = float(c1["low"])
            bottom = float(c3["high"])
            valid  = current_price < top
            gaps.append({
                "type":     "BEARISH",
                "top":      top,
                "bottom":   bottom,
                "midpoint": (top + bottom) / 2,
                "time":     c3["time"],
                "valid":    valid,
            })

    return gaps


# ──────────────────────────────────────────────────────────────
# LIQUIDITY SWEEP (Stop Hunt)
# ──────────────────────────────────────────────────────────────

def detect_liquidity_sweep(df: pd.DataFrame, swing_highs: list,
                           swing_lows: list, direction: str,
                           lookback: int = 8,
                           levels: list | None = None) -> dict:
    """
    Detecta un barrido de liquidez reciente (stop hunt) a favor del setup.

    Para BUY:  una vela reciente perforó un swing low previo (cazó los stops
      de los compradores) pero CERRÓ de vuelta por encima → el smart money
      acumuló y el precio suele revertir al alza.
    Para SELL: una vela reciente perforó un swing high previo pero cerró
      de vuelta por debajo → distribución, reversión a la baja.

    levels: lista opcional de precios significativos (equal lows/highs,
      high/low del día previo o de sesión). Si se pasa, el barrido se busca
      contra TODOS esos niveles en vez de solo el último swing previo.

    Returns: {"swept": bool, "level": float | None, "bars_ago": int | None,
              "wick_extreme": float | None}
    """
    none = {"swept": False, "level": None, "bars_ago": None, "wick_extreme": None}
    if len(df) < lookback + 2:
        return none

    recent = df.tail(lookback)
    n      = len(df)

    if direction == "BUY":
        if levels:
            refs = sorted(levels, reverse=True)  # nivel más cercano primero
        else:
            # Swing lows formados ANTES de la ventana reciente
            prior_lows = [s for s in swing_lows if s["index"] < n - lookback]
            if not prior_lows:
                return none
            refs = [prior_lows[-1]["price"]]
        for ref in refs:
            for offset, (_, candle) in enumerate(recent.iterrows()):
                low   = float(candle["low"])
                close = float(candle["close"])
                if low < ref and close > ref:
                    return {
                        "swept":        True,
                        "level":        ref,
                        "bars_ago":     len(recent) - offset - 1,
                        "wick_extreme": low,
                    }
    else:
        if levels:
            refs = sorted(levels)
        else:
            prior_highs = [s for s in swing_highs if s["index"] < n - lookback]
            if not prior_highs:
                return none
            refs = [prior_highs[-1]["price"]]
        for ref in refs:
            for offset, (_, candle) in enumerate(recent.iterrows()):
                high  = float(candle["high"])
                close = float(candle["close"])
                if high > ref and close < ref:
                    return {
                        "swept":        True,
                        "level":        ref,
                        "bars_ago":     len(recent) - offset - 1,
                        "wick_extreme": high,
                    }

    return none


# ──────────────────────────────────────────────────────────────
# DESPLAZAMIENTO (impulso direccional fuerte)
# ──────────────────────────────────────────────────────────────

def detect_displacement(df: pd.DataFrame, atr: float,
                        n_candles: int = 3,
                        body_atr: float = 0.8,
                        net_atr: float = 1.5,
                        exclude_last: bool = True) -> dict:
    """
    Detecta desplazamiento direccional fuerte en las últimas velas CERRADAS.
    Se usa como veto: nunca operar CONTRA un impulso institucional reciente
    (fix 11-jun: 5 SELL contra un rally con velas de cuerpo > 1 ATR).

    Criterio (determinista, reproducible en backtest):
      - >= 2 velas con cuerpo >= body_atr × ATR en la misma dirección Y
        movimiento neto (último close - primer open) >= net_atr × ATR
      - O una sola vela con cuerpo >= net_atr × ATR

    exclude_last: en vivo la última vela de MT5 está EN FORMACIÓN → se
      excluye; en backtest la ventana ya viene de velas cerradas → False.

    Returns: {"direction": "BULLISH"|"BEARISH"|None, "strength": float,
              "impulse_candles": int, "net_move_atr": float}
    """
    none = {"direction": None, "strength": 0.0, "impulse_candles": 0, "net_move_atr": 0.0}
    if atr is None or atr <= 0:
        return none

    window = df.iloc[-(n_candles + 1):-1] if exclude_last else df.tail(n_candles)
    if len(window) < 1:
        return none

    bull_impulses = 0
    bear_impulses = 0
    max_body_atr  = 0.0
    for _, candle in window.iterrows():
        body = float(candle["close"]) - float(candle["open"])
        body_in_atr = abs(body) / atr
        max_body_atr = max(max_body_atr, body_in_atr)
        if body_in_atr >= body_atr:
            if body > 0:
                bull_impulses += 1
            else:
                bear_impulses += 1

    net_move     = float(window["close"].iloc[-1]) - float(window["open"].iloc[0])
    net_move_atr = net_move / atr

    # Vela única gigante también cuenta como desplazamiento
    single_giant_bull = max_body_atr >= net_atr and net_move > 0
    single_giant_bear = max_body_atr >= net_atr and net_move < 0

    if (bull_impulses >= 2 and net_move_atr >= net_atr) or single_giant_bull:
        return {
            "direction":       "BULLISH",
            "strength":        round(abs(net_move_atr), 2),
            "impulse_candles": bull_impulses,
            "net_move_atr":    round(net_move_atr, 2),
        }
    if (bear_impulses >= 2 and net_move_atr <= -net_atr) or single_giant_bear:
        return {
            "direction":       "BEARISH",
            "strength":        round(abs(net_move_atr), 2),
            "impulse_candles": bear_impulses,
            "net_move_atr":    round(net_move_atr, 2),
        }
    return none


# ──────────────────────────────────────────────────────────────
# NIVELES SIGNIFICATIVOS (pools de liquidez para sweep-reversal)
# ──────────────────────────────────────────────────────────────

def get_significant_levels(df_h1: pd.DataFrame) -> dict:
    """
    Niveles donde se acumula liquidez real (stops de retail):
      - Equal highs / equal lows (find_liquidity_zones)
      - High/Low del día anterior (PDH/PDL)
      - High/Low de la sesión actual de Londres (7-12 UTC) y NY (12-21 UTC)

    Returns: {"highs": [float], "lows": [float]}
    """
    highs: list = []
    lows:  list = []
    if df_h1.empty or "time" not in df_h1.columns:
        return {"highs": highs, "lows": lows}

    swing_highs, swing_lows = find_swing_points(df_h1)
    liq = find_liquidity_zones(df_h1, swing_highs, swing_lows)
    highs += [z["price"] for z in liq.get("buy_side", [])]
    lows  += [z["price"] for z in liq.get("sell_side", [])]

    try:
        times = pd.to_datetime(df_h1["time"])
        dates = times.dt.date
        today = dates.iloc[-1]
        prev_days = dates[dates < today]
        if not prev_days.empty:
            prev_day  = prev_days.iloc[-1]
            mask_prev = dates == prev_day
            highs.append(float(df_h1.loc[mask_prev, "high"].max()))   # PDH
            lows.append(float(df_h1.loc[mask_prev, "low"].min()))     # PDL

        # Sesiones de HOY (Londres 7-12 UTC, NY 12-21 UTC)
        mask_today = dates == today
        hours      = times.dt.hour
        for h_start, h_end in ((7, 12), (12, 21)):
            mask_sess = mask_today & (hours >= h_start) & (hours < h_end)
            if mask_sess.any():
                highs.append(float(df_h1.loc[mask_sess, "high"].max()))
                lows.append(float(df_h1.loc[mask_sess, "low"].min()))
    except Exception as e:
        logger.debug(f"get_significant_levels fechas: {e}")

    # Deduplicar niveles casi idénticos (0.05%)
    def _dedup(vals: list) -> list:
        out: list = []
        for v in sorted(set(vals)):
            if not out or abs(v - out[-1]) / max(v, 1e-9) > 0.0005:
                out.append(v)
        return out

    return {"highs": _dedup(highs), "lows": _dedup(lows)}


# ──────────────────────────────────────────────────────────────
# LIQUIDEZ DE MARCO SUPERIOR (pools H4/D1 intactos)
# ──────────────────────────────────────────────────────────────

def find_htf_liquidity_pools(frames: list, current_price: float,
                             atr_h1: float, max_dist_atr: float = 1.5,
                             swing_lookback: int = 3) -> dict:
    """
    Pools de liquidez de marco superior (H4/D1) INTACTOS cerca del precio.

    Un swing high HTF que ningún high posterior superó = pool de buy-side
    liquidity intacto (stops de cortos + buy-stops de breakout acumulados).
    Un swing low HTF no perforado = pool de sell-side liquidity intacto.

    El precio actúa como IMÁN hacia estos pools: el mercado suele ir a
    barrerlos ANTES de girar. Operar de frente contra un pool cercano e
    intacto es el escenario del 11-jun (SELL bajo equal highs → rally de
    barrido → SL). Dos pools al mismo nivel se fusionan como "equal_highs"
    / "equal_lows" (liquidez doble = imán más fuerte).

    frames: lista de tuplas (df, etiqueta), ej. [(df_h4, "H4"), (df_d1, "D1")]

    Returns: {"above": [{price, tf, kind}], "below": [...]}
             — ordenados del más cercano al más lejano del precio.
    """
    out = {"above": [], "below": []}
    if not atr_h1 or atr_h1 <= 0 or not current_price:
        return out
    max_dist = atr_h1 * max_dist_atr

    for df, tf_label in frames:
        if df is None or df.empty or len(df) < swing_lookback * 2 + 2:
            continue
        swing_highs, swing_lows = find_swing_points(df, lookback=swing_lookback)
        highs = df["high"].values
        lows  = df["low"].values

        for s in swing_highs:
            later = highs[s["index"] + 1:]
            if later.size and float(later.max()) > s["price"]:
                continue  # ya barrido por un high posterior
            dist = s["price"] - current_price
            if 0 < dist <= max_dist:
                out["above"].append({"price": s["price"], "tf": tf_label,
                                     "kind": "swing_high"})

        for s in swing_lows:
            later = lows[s["index"] + 1:]
            if later.size and float(later.min()) < s["price"]:
                continue  # ya perforado por un low posterior
            dist = current_price - s["price"]
            if 0 < dist <= max_dist:
                out["below"].append({"price": s["price"], "tf": tf_label,
                                     "kind": "swing_low"})

    def _merge(pools: list, equal_kind: str) -> list:
        """Fusiona pools a < 0.08% entre sí → equal highs/lows."""
        merged: list = []
        for p in sorted(pools, key=lambda x: x["price"]):
            if merged and abs(p["price"] - merged[-1]["price"]) \
                    / max(p["price"], 1e-9) < 0.0008:
                merged[-1]["kind"] = equal_kind
                merged[-1]["tf"]   = f"{merged[-1]['tf']}+{p['tf']}" \
                    if p["tf"] not in merged[-1]["tf"] else merged[-1]["tf"]
            else:
                merged.append(dict(p))
        return merged

    # Más cercano al precio primero: above ascendente, below descendente
    out["above"] = sorted(_merge(out["above"], "equal_highs"),
                          key=lambda p: p["price"])
    out["below"] = sorted(_merge(out["below"], "equal_lows"),
                          key=lambda p: p["price"], reverse=True)
    return out


# ──────────────────────────────────────────────────────────────
# ZONAS DE LIQUIDEZ (Equal Highs / Equal Lows)
# ──────────────────────────────────────────────────────────────

def find_liquidity_zones(df: pd.DataFrame,
                         swing_highs: list,
                         swing_lows: list,
                         tolerance_pct: float = 0.001) -> dict:
    """
    Identifica zonas de liquidez donde probablemente hay stops acumulados.

    - Buy-side liquidity:  por encima de swing highs iguales (equal highs)
    - Sell-side liquidity: por debajo de swing lows iguales (equal lows)

    tolerance_pct = 0.1% de diferencia máxima para considerar dos niveles "iguales"
    """
    buy_side  = []
    sell_side = []

    # Equal highs → liquidez buy-side (stops de vendedores en corto)
    for i in range(len(swing_highs) - 1):
        for j in range(i + 1, len(swing_highs)):
            h1 = swing_highs[i]["price"]
            h2 = swing_highs[j]["price"]
            if h1 > 0 and abs(h1 - h2) / h1 < tolerance_pct:
                buy_side.append({
                    "price": max(h1, h2),
                    "type":  "equal_highs",
                })

    # Equal lows → liquidez sell-side (stops de compradores)
    for i in range(len(swing_lows) - 1):
        for j in range(i + 1, len(swing_lows)):
            l1 = swing_lows[i]["price"]
            l2 = swing_lows[j]["price"]
            if l1 > 0 and abs(l1 - l2) / l1 < tolerance_pct:
                sell_side.append({
                    "price": min(l1, l2),
                    "type":  "equal_lows",
                })

    return {"buy_side": buy_side, "sell_side": sell_side}

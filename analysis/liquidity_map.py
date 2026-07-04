"""
Mapa de liquidez rankeado — experimento sobre la idea del usuario (2026-06-18):
"a veces barre una zona pero más cerca hay otra con MÁS liquidez, retrocede y la
barre; pero las liquidez pequeñas a veces no valen la pena".

Rankea los pools de liquidez (H4/D1) por SIGNIFICANCIA, no solo por cercanía:
  - equal_highs/lows (liquidez doble) > swing simple
  - confluencia multi-timeframe (H4+D1) pesa más
  - D1 pesa más que H4
  - cercanía a número redondo (50/100) = más stops acumulados
Y detecta CASCADAS: si el pool más cercano es pequeño y detrás hay uno grande,
el precio puede barrer el pequeño y seguir hacia el grande (objetivo real).

Read-only. Uso:  PY analysis/liquidity_map.py
"""

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from analysis.market_structure import find_htf_liquidity_pools

# (paso_redondo, bonus) — números redondos donde se acumulan stops
ROUND_LEVELS = [(100.0, 1.5), (50.0, 1.0), (25.0, 0.6), (10.0, 0.3)]
MINOR_SCORE = 1.2   # por debajo de esto = liquidez "que no vale la pena"


def _round_bonus(price: float) -> float:
    best = 0.0
    for step, bonus in ROUND_LEVELS:
        nearest = round(price / step) * step
        if abs(price - nearest) <= max(1.0, step * 0.03):
            best = max(best, bonus)
    return best


def score_pool(p: dict) -> float:
    """Significancia de un pool: cuánta liquidez (stops) probablemente reposa ahí."""
    s = 2.0 if p["kind"] in ("equal_highs", "equal_lows") else 1.0
    tf = p["tf"]
    if "D1" in tf:
        s *= 1.6                 # D1 = liquidez más relevante que H4
    if "+" in tf:
        s += 1.0                 # confluencia multi-TF (mismo nivel en H4 y D1)
    s += _round_bonus(p["price"])
    return round(s, 2)


def rank_liquidity(frames: list, price: float, atr: float,
                   max_dist_atr: float = 8.0) -> dict:
    """Pools above/below con score de significancia y distancia en ATR."""
    pools = find_htf_liquidity_pools(frames, price, atr,
                                     max_dist_atr=max_dist_atr, swing_lookback=3)
    for side in ("above", "below"):
        for p in pools[side]:
            p["score"]    = score_pool(p)
            p["dist_atr"] = round(abs(p["price"] - price) / atr, 2)
            p["minor"]    = p["score"] < MINOR_SCORE
    return pools


def cascade_target(side_pools: list) -> dict:
    """
    Dado los pools de un lado (ordenados del más cercano al más lejano),
    devuelve el objetivo REAL de liquidez y si hay un barrido escalonado.
    """
    significant = [p for p in side_pools if not p["minor"]]
    if not significant:
        return {"target": None, "stepping": [], "reason": "sin liquidez significativa"}
    target = max(significant, key=lambda p: p["score"])
    # Pools menores ENTRE el precio y el objetivo = escalones de barrido
    stepping = [p for p in side_pools
                if p["dist_atr"] < target["dist_atr"] and p["minor"]]
    return {"target": target, "stepping": stepping,
            "reason": "objetivo directo" if not stepping
                      else f"barrido escalonado ({len(stepping)} menor/es antes)"}


def _fmt(p: dict) -> str:
    flag = "·minor" if p["minor"] else "★"
    return (f"{p['price']:.2f}  [{p['tf']}/{p['kind']}]  "
            f"score {p['score']}  dist {p['dist_atr']} ATR  {flag}")


def _main():
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import logging
    logging.disable(logging.CRITICAL)
    import yaml
    from data.mt5_connector import MT5Connector
    from analysis.indicators import add_indicators

    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    mt5 = MT5Connector(cfg)
    if not mt5.connect():
        print("❌ MT5 no disponible"); return
    sym = cfg.get("symbols", {}).get("primary", "XAUUSD")
    df_h1 = add_indicators(mt5.get_rates(sym, "H1", 300))
    df_h4 = add_indicators(mt5.get_rates(sym, "H4", 300))
    df_d1 = add_indicators(mt5.get_rates(sym, "D1", 120))
    tick = mt5.get_current_price(sym)
    mt5.disconnect()
    price = (float(tick["bid"]) + float(tick["ask"])) / 2
    atr = float(df_h1["atr"].iloc[-1])

    pools = rank_liquidity([(df_h4, "H4"), (df_d1, "D1")], price, atr, max_dist_atr=8.0)

    print("═" * 70)
    print(f"🧲 MAPA DE LIQUIDEZ — {sym} @ {price:.2f}  (ATR {atr:.2f})")
    print("═" * 70)
    for side, label in (("above", "ARRIBA (buy-side: stops de cortos)"),
                        ("below", "ABAJO (sell-side: stops de largos)")):
        print(f"\n{label}:")
        if not pools[side]:
            print("  (sin pools intactos en rango)")
            continue
        for p in pools[side]:
            print(f"  {_fmt(p)}")
        ct = cascade_target(pools[side])
        if ct["target"]:
            t = ct["target"]
            print(f"  → IMÁN PRINCIPAL: {t['price']:.2f} (score {t['score']}, "
                  f"{t['dist_atr']} ATR) — {ct['reason']}")
            if ct["stepping"]:
                pasos = ", ".join(f"{s['price']:.2f}" for s in ct["stepping"])
                print(f"    puede barrer primero: {pasos}  (liquidez menor, escalón)")
    print("\n" + "═" * 70)
    print("Lectura: ★ = liquidez que vale la pena; ·minor = pequeña (ignorable).")
    print("El precio tiende al imán principal; las menores son escalones de paso.")


if __name__ == "__main__":
    _main()

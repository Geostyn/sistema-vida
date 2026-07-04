"""
Radiografía en vivo — qué dice CADA módulo del cerebro sobre el XAUUSD AHORA.

A diferencia de `main.py --test` (que corre analyze() y se corta en el primer
descarte), recoge la salida de cada módulo POR SEPARADO con datos en vivo de
MT5/COMEX/macro y al final ejecuta analyze() completo para el veredicto.

Dos consumidores comparten la misma lógica (función gather):
  · CLI / RADIOGRAFIA.bat  → render_console()  (texto enriquecido)
  · Telegram /radiografia   → render_telegram() (HTML compacto, móvil)

IMPORTANTE: este módulo NO tiene efectos al importarse (no chdir, no logging,
no carga config). Todo eso vive en main_cli(). Así main.py puede importarlo
sin alterar el bot en marcha.

Ejecutar:
    PY=/c/Users/geost/AppData/Local/Python/pythoncore-3.14-64/python.exe
    $PY radiografia.py
"""

import os
import sys
from datetime import datetime, timezone

from analysis.indicators import add_indicators, get_rsi_state
from analysis.trend_filter import compute_directional_bias, counter_trend_veto
from analysis.market_structure import (
    detect_market_structure, find_order_blocks, find_fair_value_gaps,
    detect_liquidity_sweep, find_htf_liquidity_pools,
)

H = "═" * 64


# ──────────────────────────────────────────────────────────────────
# Recolección: corre cada módulo y devuelve un dict estructurado.
# Recibe los módulos ya cableados (los reutiliza el bot vía self.*).
# ──────────────────────────────────────────────────────────────────
def gather(*, cfg, symbol, mt5, news, corr, macro, ml, vp, delta,
           regime, inter, neural, quant, engine) -> dict:
    now = datetime.now(timezone.utc)
    R = {"now": now, "symbol": symbol, "ok": False}

    df_h4 = add_indicators(mt5.get_rates(symbol, "H4", 300))
    df_h1 = add_indicators(mt5.get_rates(symbol, "H1", 300))
    tick  = mt5.get_current_price(symbol)
    if df_h1.empty or df_h4.empty or not tick:
        R["error"] = "Sin datos de MT5 (¿símbolo visible en Market Watch?)"
        return R

    R["ok"] = True
    atr = float(df_h1["atr"].iloc[-1])
    bid, ask = float(tick["bid"]), float(tick["ask"])
    R.update({
        "bid": bid, "ask": ask,
        "spread": float(tick.get("spread", ask - bid) or (ask - bid)),
        "atr": atr, "atr_pct": atr / ask * 100,
        "rsi_val": float(df_h1["rsi"].iloc[-1]),
        "rsi_st": get_rsi_state(df_h1),
    })

    # 1. Régimen
    R["regime"] = regime.analyze(symbol)

    # 2. Bias / dirección
    bias, direction = compute_directional_bias(df_h4, lookback=5)
    R["bias"], R["direction"] = bias, direction
    dirs = [direction] if direction else ["BUY", "SELL"]
    R["dirs"] = dirs

    tf_cfg = cfg.get("trend_filter", {})
    try:
        dxy_score_veto = corr.get_dxy_trend().get("score")
    except Exception:
        dxy_score_veto = None
    if direction:
        R["htf_gate"] = counter_trend_veto(direction, df_h4, dxy_score_veto, tf_cfg)

    # 3. Estructura SMC H1
    struct = detect_market_structure(df_h1, lookback=5)
    obs    = find_order_blocks(df_h1, n_recent=40)
    R["struct_trend"] = struct.get("trend")
    R["last_event"]   = struct.get("last_event")
    R["n_obs_valid"]  = sum(1 for o in obs if o.get("valid"))
    R["n_obs"]        = len(obs)
    R["ob_by_dir"] = {}
    for d in dirs:
        tgt   = "BULLISH" if d == "BUY" else "BEARISH"
        price = ask if d == "BUY" else bid
        R["ob_by_dir"][d] = engine._find_nearest_ob(obs, price, d, atr, tgt)

    # 4. DXY
    R["dxy"] = corr.get_dxy_trend()

    # 5. Macro global
    R["macro"] = macro.get_macro_bias()

    # 6. Intermarket (por dirección)
    R["inter_by_dir"] = {d: inter.get_intermarket_score(d) for d in dirs}

    # 7. Volume Profile COMEX (por dirección)
    R["vp_by_dir"] = {}
    for d in dirs:
        price = ask if d == "BUY" else bid
        try:
            R["vp_by_dir"][d] = vp.get_confluence(price=price, direction=d, atr=atr, spot_price=price)
        except Exception as e:
            R["vp_by_dir"][d] = (0.0, f"no disponible ({e})")

    # 8. Delta / Footprint (por dirección)
    R["delta_by_dir"] = {}
    for d in dirs:
        try:
            R["delta_by_dir"][d] = delta.get_confluence(symbol=symbol, direction=d, df_h1=df_h1)
        except Exception as e:
            R["delta_by_dir"][d] = (0.0, f"no disponible ({e})")

    # 9. Quant
    try:
        R["quant"] = quant.analyze(symbol, df_h1, atr=atr)
    except Exception:
        R["quant"] = {}

    # 10. Liquidez
    R["sweep_by_dir"] = {
        d: detect_liquidity_sweep(df_h1, struct["swing_highs"], struct["swing_lows"], d)
        for d in dirs
    }
    try:
        R["n_fvg"] = sum(1 for g in find_fair_value_gaps(df_h1, n_recent=40) if g.get("valid"))
    except Exception:
        R["n_fvg"] = None
    R["pool_by_dir"] = {}
    try:
        df_d1 = add_indicators(mt5.get_rates(symbol, "D1", 90))
        for d in dirs:
            price = ask if d == "BUY" else bid
            pools = find_htf_liquidity_pools([(df_h4, "H4"), (df_d1, "D1")], price, atr,
                                             max_dist_atr=1.5, swing_lookback=3)
            in_path = pools["above"] if d == "SELL" else pools["below"]
            R["pool_by_dir"][d] = in_path[0] if in_path else None
    except Exception:
        R["pool_by_dir"] = {d: None for d in dirs}

    # 11. ML ensemble — proba EXACTA si hay señal (la del propio analyze, abajo),
    # si no, preview enriquecido con las features reales ya recogidas arriba.
    R["ml_trained"] = bool(getattr(ml, "models", None))
    R["ml_count"]   = getattr(ml, "last_count", ml.get_model_stats().get("n_trades", 0))
    R["ml_veto"]    = float(cfg.get("risk", {}).get("ml_veto_threshold", 0.40))
    R["ml_proba"]   = None
    R["ml_exact"]   = False
    if R["ml_trained"] and direction:
        rg   = R["regime"]
        q    = R["quant"]
        ic   = R["inter_by_dir"].get(direction, {}) or {}
        ry   = ic.get("real_yields", {}) or {}
        cot  = ic.get("cot", {}) or {}
        pool = R["pool_by_dir"].get(direction)
        px   = ask if direction == "BUY" else bid
        htf_liq = round(abs(pool["price"] - px) / atr, 2) if pool else 99.0
        ob   = R["ob_by_dir"].get(direction)
        # Las claves coinciden con LearningEngine._extract_features (timestamp
        # → hora/sesión; el resto son las mismas features que en vivo).
        preview = {
            "timestamp":      now.isoformat(),
            "direction":      direction,
            "rsi_state":      R["rsi_st"],
            "bias_h4":        bias,
            "ob_type":        ob["type"] if ob else ("BULLISH" if direction == "BUY" else "BEARISH"),
            "news_blackout":  int(R.get("news_blackout", 0)),
            "atr_pct":        round(R["atr_pct"], 3),
            "hurst":          float(rg.get("hurst", 0.5)),
            "adx":            float(rg.get("adx", 20)),
            "vp_score":       float(R["vp_by_dir"][direction][0]),
            "delta_score":    float(R["delta_by_dir"][direction][0]),
            "sweep_score":    1.0 if R["sweep_by_dir"][direction]["swept"] else 0.0,
            "htf_liq_dist":   float(htf_liq),
            "inter_score":    float(ic.get("gold_bias_score", 0.0)),
            "real_yield_imp": float(ry.get("gold_impact", 0.0)),
            "cot_impact":     float(cot.get("gold_impact", 0.0)),
            "cot_percentile": float(cot.get("percentile", 0.5)),
            "garch_vol":      float(q.get("garch_vol", 1.0)) if q else 1.0,
            "kalman_slope":   float(q.get("kalman_slope", 0.0)) if q else 0.0,
            "source":         1,
        }
        try:
            R["ml_proba"] = ml.predict_win_probability(preview)
        except Exception:
            R["ml_proba"] = None

    # 12. Neural LSTM (sombra)
    R["neural_avail"] = bool(neural and neural.available())
    R["neural_by_dir"] = {}
    if R["neural_avail"]:
        for d in dirs:
            try:
                R["neural_by_dir"][d] = neural.predict_proba(df_h1, d)
            except Exception:
                R["neural_by_dir"][d] = None

    # 13. Noticias
    try:
        avoid = cfg.get("sessions", {}).get("avoid_news_minutes", 30)
        ns = news.is_news_blackout(minutes_buffer=avoid)
        R["news_blackout"] = bool(ns.get("blackout"))
        R["news_reason"]   = ns.get("reason", "")
        cal = news.get_daily_summary() or []
        R["news_events"] = [e for e in cal if e.get("impact") in ("HIGH", "MEDIUM")][:4]
    except Exception:
        R["news_blackout"], R["news_reason"], R["news_events"] = False, "", []

    # 14. Sentimiento de noticias / riesgo geopolítico (MODO SOMBRA)
    try:
        from data.news_sentiment import NewsSentiment
        R["news_sent"] = NewsSentiment(cfg).get_risk_score()
    except Exception as e:
        R["news_sent"] = {"error": str(e)}

    # Veredicto: analyze() completo
    try:
        sig = engine.analyze(symbol)
    except Exception as e:
        sig = None
        engine.last_discard[symbol] = {"reason": f"error en analyze(): {e}"}
    R["verdict_signal"] = sig
    R["verdict_reason"] = (engine.last_discard.get(symbol, {}) or {}).get("reason", "desconocido")
    # Si hubo señal, la proba ML EXACTA es la que calculó analyze() (no la del preview)
    if sig and sig.get("ml_proba") is not None:
        R["ml_proba"] = float(sig["ml_proba"])
        R["ml_exact"] = True
    return R


def _ev_time(e):
    t = e.get("time")
    return t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)[:5]


# ──────────────────────────────────────────────────────────────────
# Render CONSOLA (RADIOGRAFIA.bat) — detallado
# ──────────────────────────────────────────────────────────────────
def render_console(R: dict) -> str:
    if not R.get("ok"):
        return f"❌ {R.get('error', 'sin datos')}"

    dirs = R["dirs"]
    out = [H, f"🧠 RADIOGRAFÍA EN VIVO — {R['symbol']} — {R['now']:%Y-%m-%d %H:%M} UTC", H]

    def sec(emoji, title):
        out.append(f"\n{emoji} {title}")
        out.append("─" * 64)

    sec("0️⃣", "MERCADO AHORA")
    out.append(f"  Precio:  bid {R['bid']:.2f} / ask {R['ask']:.2f}   spread {R['spread']:.2f}")
    out.append(f"  ATR H1:  {R['atr']:.2f}   ({R['atr_pct']:.2f}% del precio)")
    out.append(f"  RSI H1:  {R['rsi_val']:.1f}  → {R['rsi_st']}")

    rg = R["regime"]
    sec("1️⃣", "RÉGIMEN DE MERCADO  (Hurst + ADX + Volatilidad)")
    out.append(f"  Régimen:        {rg.get('regime')}")
    out.append(f"  ADX H4:         {rg.get('adx')}   Hurst: {rg.get('hurst')}")
    out.append(f"  Volatilidad:    {rg.get('vol_regime')}  (ratio {rg.get('vol_ratio')})")
    out.append(f"  Trading permitido: {rg.get('trade_allowed')}"
               f"   min_confluences override: {rg.get('min_confluences_override')}")

    sec("2️⃣", "BIAS DIRECCIONAL H4  (motor compartido con el backtester)")
    out.append(f"  Bias H4:    {R['bias']}")
    out.append(f"  Dirección que evalúa el resto del cerebro: {R['direction'] or '— (NEUTRAL)'}")
    if R.get("htf_gate"):
        blocked, reason = R["htf_gate"]
        out.append(f"  Puerta tendencia HTF: {'🔴 BLOQUEA — ' + reason if blocked else '🟢 pasa'}")

    sec("3️⃣", "ESTRUCTURA SMC H1")
    out.append(f"  Tendencia estructural: {R['struct_trend']}")
    if R["last_event"]:
        out.append(f"  Último evento: {R['last_event']['type']} {R['last_event']['direction']}")
    out.append(f"  Order Blocks válidos: {R['n_obs_valid']} de {R['n_obs']} detectados")
    for d in dirs:
        ob = R["ob_by_dir"].get(d)
        price = R["ask"] if d == "BUY" else R["bid"]
        if ob:
            mid = (ob["top"] + ob["bottom"]) / 2
            out.append(f"   · {d}: OB {ob['type']} en {ob['bottom']:.2f}–{ob['top']:.2f} "
                       f"(dist {abs(price-mid)/R['atr']:.1f} ATR)")
        else:
            out.append(f"   · {d}: sin OB válido cerca del precio")

    dxy = R["dxy"]
    sec("4️⃣", "DXY SINTÉTICO  (correlación inversa con el oro)")
    out.append(f"  Tendencia: {dxy.get('trend')}   score {dxy.get('score')}")
    out.append(f"  EMA8 {dxy.get('ema8')} vs EMA21 {dxy.get('ema21')}   "
               f"momentum {dxy.get('momentum')}%   último {dxy.get('dxy_last')}")
    for d in dirs:
        aligned = (dxy.get('trend') == "BEARISH") if d == "BUY" else (dxy.get('trend') == "BULLISH")
        out.append(f"   · {d}: {'🟢 alineado (+1.5)' if aligned else '⚪ no aporta'}")

    mb = R["macro"]; det = mb.get("details", {})
    sec("5️⃣", "MACRO GLOBAL  (DXY + Yields 10Y + VIX)")
    out.append(f"  Sesgo oro: {mb.get('gold_bias')}   score {mb.get('score')}")
    if "dxy" in det:
        out.append(f"  DXY:    {det['dxy'].get('valor')}  ({det['dxy'].get('cambio_6h')}% 6h)  "
                   f"{det['dxy'].get('ema_trend')}  [{det['dxy'].get('fuente')}]")
    if "bonds_10y" in det:
        out.append(f"  10Y:    {det['bonds_10y'].get('valor')}%  "
                   f"({det['bonds_10y'].get('cambio_6h')} 6h)  {det['bonds_10y'].get('trend')}")
    if "vix" in det:
        out.append(f"  VIX:    {det['vix']}")

    sec("6️⃣", "INTERMARKET  (rendimientos reales + COT + oro/plata + riesgo)")
    for d in dirs:
        ic = R["inter_by_dir"][d]
        out.append(f"   · {d}: gold_score {ic.get('gold_bias_score')}  "
                   f"alineado {ic.get('aligned_score')}  → confluencia +{ic.get('confluence')}")
        for x in ic.get("details", []):
            out.append(f"        - {x}")

    sec("7️⃣", "VOLUME PROFILE COMEX  (futuros GC=F: POC/VAH/VAL)")
    for d in dirs:
        sc, desc = R["vp_by_dir"][d]
        out.append(f"   · {d}: +{sc}  {desc or '(sin confluencia en esta zona)'}")

    sec("8️⃣", "DELTA / FOOTPRINT  (tick data MT5, barra H1)")
    for d in dirs:
        sc, desc = R["delta_by_dir"][d]
        out.append(f"   · {d}: {sc:+}  {desc or '(neutral)'}")

    q = R["quant"]
    sec("9️⃣", "QUANT  (GARCH volatilidad prevista + Kalman tendencia)")
    if q:
        gv = float(q.get("garch_vol", 1.0))
        out.append(f"  GARCH vol ratio: {gv:.2f}  "
                   f"{'⚠️ alta → exige más calidad' if gv >= 1.6 else '🟢 normal'}")
        out.append(f"  Kalman slope:    {float(q.get('kalman_slope', 0.0)):+.4f}")
    else:
        out.append("  No disponible")

    sec("🔟", "LIQUIDEZ SMC")
    for d in dirs:
        sw = R["sweep_by_dir"][d]
        if sw["swept"]:
            out.append(f"   · {d}: 🟢 sweep+reclaim a favor en {sw['level']:.2f} "
                       f"(hace {sw['bars_ago']} velas) → +1.0")
        else:
            out.append(f"   · {d}: sin sweep a favor reciente")
    if R["n_fvg"] is not None:
        out.append(f"  Fair Value Gaps válidos: {R['n_fvg']}")
    for d in dirs:
        pool = R["pool_by_dir"].get(d)
        price = R["ask"] if d == "BUY" else R["bid"]
        if pool:
            out.append(f"   · {d}: ⚠️ pool {pool['kind']} {pool['tf']} en {pool['price']:.2f} "
                       f"({abs(pool['price']-price)/R['atr']:.1f} ATR) — imán de liquidez en contra")
        else:
            out.append(f"   · {d}: 🟢 sin pool HTF intacto en contra")

    sec("1️⃣1️⃣", "ML ENSEMBLE  (RandomForest + GBM + …)")
    out.append(f"  Modelos entrenados: {'sí' if R['ml_trained'] else 'no'}   "
               f"trades en historial: {R['ml_count']}")
    if R["ml_proba"] is not None:
        flag = "🔴 VETARÍA" if (R["ml_count"] >= 40 and R["ml_proba"] < R["ml_veto"]) else "🟢 pasa"
        nota = "exacta (de la señal)" if R.get("ml_exact") else "estimada del contexto (sin señal)"
        out.append(f"  Probabilidad de WIN: {R['ml_proba']:.0%}  "
                   f"(veto < {R['ml_veto']:.0%}) {flag} — {nota}")
    else:
        out.append("  (sin modelo entrenado o bias neutral → proba neutra 50%)")

    sec("1️⃣2️⃣", "RED NEURONAL LSTM  (modo sombra — no veta todavía)")
    if R["neural_avail"]:
        for d in dirs:
            p = R["neural_by_dir"].get(d)
            out.append(f"   · {d}: proba {p:.0%}" if p is not None else f"   · {d}: error")
    else:
        out.append("  Modelo LSTM no entrenado / no disponible (esperado hasta acumular datos)")

    sec("1️⃣3️⃣", "NOTICIAS / CALENDARIO ECONÓMICO")
    out.append(f"  Blackout activo: {'🔴 SÍ — ' + R['news_reason'] if R['news_blackout'] else '🟢 no'}")
    if R["news_events"]:
        out.append("  Próximos eventos relevantes:")
        for e in R["news_events"]:
            out.append(f"   {'🔴' if e.get('impact')=='HIGH' else '🟡'} {_ev_time(e)} — {e.get('event')}")
    else:
        out.append("  Sin eventos HIGH/MEDIUM próximos.")

    ns = R.get("news_sent") or {}
    if ns and not ns.get("error"):
        sec("1️⃣4️⃣", "SENTIMIENTO DE NOTICIAS  (sombra — guerras/Fed/risk-off, no opera)")
        emoji = "🟢" if ns.get("gold_bias") == "BULLISH" else "🔴" if ns.get("gold_bias") == "BEARISH" else "⚪"
        out.append(f"  Sesgo oro: {emoji} {ns.get('gold_bias')}  score {ns.get('score')}  "
                   f"(risk-off {ns.get('risk_off_hits')} vs risk-on {ns.get('risk_on_hits')})"
                   f"{'  ⚠️ ESCALADA' if ns.get('escalation') else ''}")
        out.append(f"  {ns.get('n_headlines')} titulares [{ns.get('source')}]:")
        for h in ns.get("top", [])[:3]:
            out.append(f"    · {h[:90]}")

    out.append("\n" + H)
    out.append("🎯 VEREDICTO DEL CEREBRO  (analyze() completo, mismas reglas que en vivo)")
    out.append(H)
    sig = R["verdict_signal"]
    if sig:
        out.append(f"  ✅ SEÑAL {sig['direction']} {R['symbol']}")
        out.append(f"     Confluencias: {sig['confluences']}/16.5   "
                   f"confianza {sig['confidence']:.0%}   R:R 1:{sig['rr']}")
        out.append(f"     Entry {sig['entry']}  SL {sig['sl']}  "
                   f"TP1 {sig['tp1']}  TP2 {sig.get('tp2')}")
        out.append("     Confluencias activas:")
        for c in sig.get("confluence_details", []):
            out.append(f"       • {c}")
    else:
        out.append(f"  ⛔ SIN SEÑAL — motivo: {R['verdict_reason']}")
        out.append("\n  (No es un error: el cerebro evaluó todo y decidió NO operar ahora.)")
    out.append("\n" + H)
    return "\n".join(out)


# ──────────────────────────────────────────────────────────────────
# Render TELEGRAM (/radiografia) — HTML compacto para móvil
# ──────────────────────────────────────────────────────────────────
def render_telegram(R: dict) -> str:
    if not R.get("ok"):
        return f"❌ Radiografía: {R.get('error', 'sin datos')}"

    d  = R["direction"] or R["dirs"][0]   # dirección principal a resumir
    rg = R["regime"]
    dxy = R["dxy"]
    mb = R["macro"]
    ic = R["inter_by_dir"][d]
    vp_sc = R["vp_by_dir"][d][0]
    dl_sc = R["delta_by_dir"][d][0]
    q  = R["quant"]
    sw = R["sweep_by_dir"][d]
    pool = R["pool_by_dir"].get(d)

    L = [f"🧠 <b>RADIOGRAFÍA {R['symbol']}</b> — {R['now']:%H:%M} UTC",
         f"Precio {R['ask']:.2f} | ATR {R['atr']:.1f} | RSI {R['rsi_val']:.0f} {R['rsi_st']}",
         "━━━━━━━━━━━━━━━━━━━━━━"]

    L.append(f"1️⃣ Régimen: <b>{rg.get('regime')}</b> (ADX {rg.get('adx')} Hurst {rg.get('hurst')})")
    gate = ""
    if R.get("htf_gate"):
        gate = " 🔴HTF" if R["htf_gate"][0] else " 🟢HTF"
    L.append(f"2️⃣ Bias H4: <b>{R['bias']}</b> → {R['direction'] or 'NEUTRAL'}{gate}")
    ob = R["ob_by_dir"].get(d)
    ob_str = f"OB {ob['bottom']:.0f}–{ob['top']:.0f}" if ob else "sin OB cerca"
    L.append(f"3️⃣ SMC H1: {R['struct_trend']} | {R['n_obs_valid']} OB | {ob_str}")
    dxy_al = (dxy.get('trend') == "BEARISH") if d == "BUY" else (dxy.get('trend') == "BULLISH")
    L.append(f"4️⃣ DXY: {dxy.get('trend')} {'🟢+1.5' if dxy_al else '⚪'}")
    L.append(f"5️⃣ Macro oro: <b>{mb.get('gold_bias')}</b> (score {mb.get('score')})")
    L.append(f"6️⃣ Intermarket: +{ic.get('confluence')} "
             f"(gold {ic.get('gold_bias_score')})")
    L.append(f"7️⃣ VP COMEX: +{vp_sc}   8️⃣ Delta: {dl_sc:+}")
    if q:
        L.append(f"9️⃣ Quant: GARCH {float(q.get('garch_vol',1)):.2f} | "
                 f"Kalman {float(q.get('kalman_slope',0)):+.2f}")
    liq = f"sweep✅{sw['level']:.0f}" if sw["swept"] else "sin sweep"
    liq += f" | pool⚠️{pool['price']:.0f}" if pool else ""
    L.append(f"🔟 Liquidez {d}: {liq}")
    if R["ml_proba"] is not None:
        flag = "🔴" if (R["ml_count"] >= 40 and R["ml_proba"] < R["ml_veto"]) else "🟢"
        L.append(f"1️⃣1️⃣ ML: {R['ml_proba']:.0%} {flag} ({R['ml_count']} trades)")
    else:
        L.append(f"1️⃣1️⃣ ML: sin modelo ({R['ml_count']} trades)")
    if R["neural_avail"] and R["neural_by_dir"].get(d) is not None:
        L.append(f"1️⃣2️⃣ LSTM: {R['neural_by_dir'][d]:.0%}")
    L.append(f"1️⃣3️⃣ News: {'🔴 ' + R['news_reason'] if R['news_blackout'] else '🟢 libre'}")
    ns = R.get("news_sent") or {}
    if ns and not ns.get("error") and ns.get("n_headlines"):
        esc = " ⚠️ESCALADA" if ns.get("escalation") else ""
        L.append(f"1️⃣4️⃣ Sentimiento oro: <b>{ns.get('gold_bias')}</b> "
                 f"(off {ns.get('risk_off_hits')}/on {ns.get('risk_on_hits')}){esc}")
    L.append("━━━━━━━━━━━━━━━━━━━━━━")

    sig = R["verdict_signal"]
    if sig:
        L.append(f"🎯 <b>SEÑAL {sig['direction']}</b> — {sig['confluences']}/16.5 conf, "
                 f"R:R 1:{sig['rr']}")
        L.append(f"Entry {sig['entry']} | SL {sig['sl']} | TP1 {sig['tp1']}")
    else:
        L.append(f"🎯 <b>SIN SEÑAL</b>\n{R['verdict_reason']}")
    return "\n".join(L)


# ──────────────────────────────────────────────────────────────────
# CLI — cablea los módulos y muestra el informe de consola
# ──────────────────────────────────────────────────────────────────
def main_cli():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _root = os.path.dirname(os.path.abspath(__file__))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    os.chdir(_root)

    import logging
    logging.disable(logging.CRITICAL)

    import yaml
    from data.mt5_connector import MT5Connector
    from data.news_feed import NewsFeed
    from data.macro_feed import MacroFeed
    from data.intermarket_feed import IntermarketFeed
    from analysis.correlation_engine import CorrelationEngine
    from analysis.volume_profile import VolumeProfileEngine
    from analysis.delta_engine import DeltaEngine
    from analysis.market_regime import MarketRegimeEngine
    from analysis.quant_engine import QuantEngine
    from ml.learning_engine import LearningEngine
    from ml.neural_engine import NeuralEngine
    from analysis.signal_engine import SignalEngine

    with open("config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    symbol = cfg.get("symbols", {}).get("primary", "XAUUSD")

    mt5 = MT5Connector(cfg)
    if not mt5.connect():
        print("❌ No se pudo conectar a MT5. Abre MetaTrader 5 y reintenta.")
        return
    news   = NewsFeed(cfg.get("apis", {}).get("finnhub_key", ""))
    corr   = CorrelationEngine(mt5)
    macro  = MacroFeed(correlation_engine=corr)
    ml     = LearningEngine(cfg.get("logging", {}).get("db_path", "logs/trades.db"))
    vp     = VolumeProfileEngine(cfg)
    delta  = DeltaEngine(mt5)
    regime = MarketRegimeEngine(mt5)
    inter  = IntermarketFeed(cfg, mt5_connector=mt5)
    neural = NeuralEngine()
    quant  = QuantEngine(cfg)
    engine = SignalEngine(
        mt5, news, cfg, correlation_engine=corr, macro_feed=macro, learning_engine=ml,
        volume_profile=vp, delta_engine=delta, regime_engine=regime,
        intermarket_feed=inter, neural_engine=neural, quant_engine=quant,
    )

    R = gather(cfg=cfg, symbol=symbol, mt5=mt5, news=news, corr=corr, macro=macro,
               ml=ml, vp=vp, delta=delta, regime=regime, inter=inter,
               neural=neural, quant=quant, engine=engine)
    print(render_console(R))
    mt5.disconnect()


if __name__ == "__main__":
    main_cli()

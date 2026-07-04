"""
unified_backtester — Corre el SignalEngine REAL barra a barra sobre histórico.

El desbloqueo de PLAN-FABLE (P1): hasta ahora el backtester legacy reimplementaba
la estrategia (subset crudo); este backtester ejecuta `SignalEngine.analyze()`
—el mismo código del vivo— sobre el HistoricalConnector, así `min_confluences`,
pesos y módulos pasan a ser VALIDABLES con datos.

Wiring espejo de main.py:
  REALES : CorrelationEngine (DXY sintético, memoizado por vela H4),
           MarketRegimeEngine (reloj inyectado), QuantEngine (GARCH sin caché)
  MOCKS  : news (+1.0 fija), macro (+0.3 fija)
  None   : vp / delta / intermarket / ml / neural  → máx offline 12.3 de 16.5

⚠️ Los umbrales aquí viven en ESCALA OFFLINE. El gap medio Δ̄ vs el vivo se mide
con backtest/replay_validate.py — NUNCA copiar un umbral de aquí a config.yaml.

Uso (NO necesita MT5 — corre del caché en disco, con el bot vivo):
    python backtest/unified_backtester.py --days 30
    python backtest/unified_backtester.py --from 2025-07-01 --to 2026-07-01
    python backtest/unified_backtester.py --days 365 --min-confluences 0 --candidates
    python backtest/unified_backtester.py --days 30 --spread 0.40 --stride-garch 4
"""

import os
import sys
import json
import time
import copy
import argparse
import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.historical_connector import HistoricalConnector
from backtest.neutral_mocks import NewsNeutralMock, MacroNeutralMock
from backtest.backtester import _simulate_trade_managed, _calculate_metrics

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s - %(message)s")
logger = logging.getLogger("unified")

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


# ── Reloj del backtest ───────────────────────────────────────────────

class BacktestClock:
    """El engine pide UTC aware; el cursor vive en hora servidor naive."""

    def __init__(self, offset_hours: float):
        self._offset = timedelta(hours=offset_hours)
        self._t: pd.Timestamp | None = None

    def set_server_now(self, t_server) -> None:
        self._t = pd.Timestamp(t_server)

    def __call__(self) -> datetime:
        if self._t is None:
            raise RuntimeError("BacktestClock sin fijar")
        return (self._t - self._offset).to_pydatetime().replace(tzinfo=timezone.utc)


class CachedCorrelationEngine:
    """Memoiza get_full_context por (vela H4, símbolo, dirección) — el DXY
    sintético lee 6 pares en cada llamada y solo cambia con la vela."""

    def __init__(self, inner, clock: BacktestClock):
        self._inner = inner
        self._clock = clock
        self._memo: dict = {}

    def get_full_context(self, symbol: str, direction: str) -> dict:
        bucket = self._clock().replace(minute=0, second=0, microsecond=0)
        bucket = bucket.replace(hour=bucket.hour - bucket.hour % 4)
        key = (bucket, symbol, direction)
        if key not in self._memo:
            if len(self._memo) > 512:
                self._memo.clear()
            self._memo[key] = self._inner.get_full_context(symbol, direction)
        return self._memo[key]

    def __getattr__(self, name):
        return getattr(self._inner, name)


class StridedQuant:
    """Opcional (--stride-garch N): recalcula GARCH solo cada N barras y
    reutiliza el resultado entre medias. SOLO exploración, no para baseline."""

    def __init__(self, inner, stride: int):
        self._inner = inner
        self._stride = max(1, int(stride))
        self._bar = -1
        self._last: dict | None = None

    def set_bar(self, i: int) -> None:
        self._bar = i

    def analyze(self, symbol: str, df_h1, atr=None) -> dict:
        if self._last is None or self._bar % self._stride == 0:
            self._last = self._inner.analyze(symbol, df_h1, atr=atr)
        return self._last

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ── Construcción del engine (espejo de main.py) ─────────────────────

def load_config() -> dict:
    import yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_backtest_engine(config: dict, connector: HistoricalConnector,
                          clock: BacktestClock, stride_garch: int = 1):
    """Wiring espejo de main.py: correlation/regime/quant REALES con el
    connector histórico; news/macro mock; vp/delta/intermarket/ml/neural None."""
    from analysis.signal_engine import SignalEngine
    from analysis.correlation_engine import CorrelationEngine
    from analysis.market_regime import MarketRegimeEngine
    from analysis.quant_engine import QuantEngine

    corr   = CachedCorrelationEngine(CorrelationEngine(connector), clock)
    regime = MarketRegimeEngine(connector, clock=clock)
    quant  = QuantEngine(config)
    if stride_garch > 1:
        quant = StridedQuant(quant, stride_garch)

    engine = SignalEngine(
        connector, NewsNeutralMock(), config,
        correlation_engine=corr,
        macro_feed=MacroNeutralMock(),
        learning_engine=None,
        volume_profile=None,
        delta_engine=None,
        regime_engine=regime,
        intermarket_feed=None,
        neural_engine=None,
        quant_engine=quant,
        clock=clock,
    )
    return engine, quant


# ── Bucle principal ──────────────────────────────────────────────────

def run_unified(symbol: str = "XAUUSD", t_from=None, t_to=None, days: int = 30,
                min_confluences: float | None = None, candidates: bool = False,
                spread: float = 0.30, stride_garch: int = 1,
                record_discards: bool = False, quiet: bool = False) -> dict:
    """
    Corre analyze() al CIERRE de cada vela H1 del rango (hora servidor).
    Señales 24h como el vivo; la EJECUCIÓN (equity) se simula solo dentro
    de la sesión y respetando max_simultaneous — mismas puertas que main.py.
    """
    config = copy.deepcopy(load_config())
    # Cachés por reloj de pared contaminan el replay → GARCH siempre fresco
    config.setdefault("quant", {})["garch_cache_min"] = 0
    if min_confluences is not None:
        config.setdefault("risk", {})["min_confluences"] = float(min_confluences)

    hc = HistoricalConnector(spread_xauusd=spread)
    offset = hc.utc_offset_hours()
    clock = BacktestClock(offset)
    engine, quant = build_backtest_engine(config, hc, clock, stride_garch)

    # Serie H1 completa (con futuro) para simular los trades
    h1_entry = hc._load(symbol, "H1")
    if h1_entry is None:
        raise RuntimeError("Falta caché XAUUSD_H1 — corre preload_history.py")
    h1_full  = h1_entry["df"]
    h1_opens = h1_entry["open_ns"]

    # Rango del backtest en hora SERVIDOR
    if t_to is None:
        t_to = pd.Timestamp(h1_full["time"].iloc[-1]) + pd.Timedelta(hours=1)
    else:
        t_to = pd.Timestamp(t_to) + pd.Timedelta(hours=offset)
    if t_from is None:
        t_from = t_to - pd.Timedelta(days=days)
    else:
        t_from = pd.Timestamp(t_from) + pd.Timedelta(hours=offset)

    # Parámetros de gestión/costes — MISMA fuente que backtester legacy y vivo
    risk_cfg    = config.get("risk", {})
    sess_cfg    = config.get("sessions", {}).get("allowed_hours_utc", {})
    sess_start  = int(sess_cfg.get("start", 7))
    sess_end    = int(sess_cfg.get("end", 21))
    blocked     = config.get("sessions", {}).get("blocked_hours_utc", []) or []
    max_sim     = int(risk_cfg.get("max_simultaneous", 2))
    risk_pct    = float(risk_cfg.get("risk_per_trade", 0.01))
    capital0    = float(risk_cfg.get("capital", 10000))
    mgmt        = config.get("trading", {}).get("management", {}) or {}
    managed     = bool(mgmt.get("enabled", True))
    partial_pct = float(mgmt.get("partial_close_pct", 0.5))
    trail_mult  = float(mgmt.get("trail_atr_mult", 2.0))
    fill_window = int(config.get("trading", {}).get("order_expiry_hours", 8))
    comm_lot    = float(config.get("backtest", {}).get("commission_per_lot", 7.0))

    signals_log, trades, equity = [], [], [capital0]
    discards: dict = {}
    active: list = []   # índices de barra donde el trade/orden deja de ocupar slot

    closes = list(hc.iter_h1_closes(symbol, t_from, t_to))
    n = len(closes)
    t0 = time.time()
    if not quiet:
        print(f"Backtest unificado {symbol} | {t_from} → {t_to} (servidor, "
              f"UTC{offset:+.0f}) | {n} velas H1 | spread {spread} | "
              f"min_conf {config['risk'].get('min_confluences')}")

    for k, t_close in enumerate(closes):
        if not quiet and k and k % 200 == 0:
            el = time.time() - t0
            eta = el / k * (n - k)
            print(f"  [{k/n*100:5.1f}%] señales: {len(signals_log)} | trades: "
                  f"{len(trades)} | {el:.0f}s (ETA {eta/60:.1f} min)")

        hc.set_now(t_close)
        clock.set_server_now(t_close)
        if hasattr(quant, "set_bar"):
            quant.set_bar(k)

        utc_hour = int((t_close - pd.Timedelta(hours=offset)).hour)
        in_session = (sess_start <= utc_hour < sess_end) and utc_hour not in blocked

        try:
            sig = engine.analyze(symbol)
        except Exception as e:
            logger.warning(f"analyze() @ {t_close}: {e}")
            continue

        if sig is None:
            if record_discards:
                reason = (engine.last_discard.get(symbol) or {}).get("reason", "?")
                key = reason.split("(")[0].strip()[:60]
                discards[key] = discards.get(key, 0) + 1
            continue

        # Barra recién cerrada (la señal se evalúa en su cierre)
        bar_idx = int(np.searchsorted(h1_opens, np.datetime64(t_close - pd.Timedelta(hours=1)),
                                      side="left"))
        rec = {
            "bar_time_server": str(t_close - pd.Timedelta(hours=1)),
            "signal_time_utc": sig["timestamp"],
            "utc_hour":        utc_hour,
            "in_session":      in_session,
            "direction":       sig["direction"],
            "entry":           sig["entry"],  "sl": sig["sl"],
            "tp1":             sig["tp1"],    "tp2": sig["tp2"],
            "rr":              sig["rr"],
            "confluences":     sig["confluences"],
            "bias_h4":         sig["bias_h4"],
            "ob_type":         sig["ob_type"],
            "regime":          sig["regime"],
            "regime_adx":      sig["regime_adx"],
            "rsi_state":       sig["rsi_state"],
            "sweep_score":     sig["sweep_score"],
            "fvg_score":       sig["fvg_score"],
            "m15_aligned":     sig["m15_aligned"],
            "pairs_score":     sig["pairs_score"],
            "dxy_aligned":     sig["dxy_aligned"],
            "mtf_aligned":     sig["mtf_aligned"],
            "atr":             sig["atr"],
            "entry_type":      sig.get("entry_type"),
            "garch_vol":       sig.get("garch_vol"),
            "executed":        False,
        }

        # ── Ejecución simulada: mismas puertas que el vivo ──
        active = [e for e in active if e > bar_idx]
        if in_session and len(active) < max_sim:
            outcome = _simulate_trade_managed(
                h1_full, bar_idx, sig["direction"], float(sig["entry"]),
                float(sig["sl"]), float(sig["tp1"]),
                float(sig["tp2"] or sig["tp1"]), float(sig["atr"]),
                managed=managed, partial_pct=partial_pct,
                trail_mult=trail_mult, fill_window=fill_window, max_bars=80,
            )
            if outcome["result"] == "NO_FILL":
                active.append(bar_idx + fill_window)   # la LIMIT ocupa slot hasta expirar
            elif outcome["result"] != "OPEN":
                active.append(bar_idx + max(1, outcome["bars"]))
                sl_dist      = abs(float(sig["entry"]) - float(sig["sl"]))
                value_per_pt = 100.0  # XAUUSD $100/lote/punto
                lot_est      = (equity[-1] * risk_pct) / (sl_dist * value_per_pt) if sl_dist > 0 else 0.01
                comm_pct     = (lot_est * comm_lot) / equity[-1]
                pnl_rr  = outcome["pnl_r"]
                pnl_pct = pnl_rr * risk_pct - comm_pct
                equity.append(equity[-1] * (1 + pnl_pct))
                rec["executed"] = True
                trades.append({
                    "entry_time":    rec["bar_time_server"],
                    "exit_time":     outcome["exit_time"],
                    "direction":     sig["direction"],
                    "hour_utc":      utc_hour,
                    "result":        outcome["result"],
                    "pnl_rr":        round(pnl_rr, 2),
                    "pnl_pct":       round(pnl_pct * 100, 3),
                    "bars_to_close": outcome["bars"],
                    "confluences":   sig["confluences"],
                    "rr":            sig["rr"],
                    "bias":          sig["bias_h4"],
                })

        signals_log.append(rec)

    metrics = _calculate_metrics(trades, equity) if trades else {"error": "sin trades"}
    elapsed = time.time() - t0

    out = {
        "engine":     "unified",
        "symbol":     symbol,
        "from":       str(t_from), "to": str(t_to),
        "utc_offset": offset,
        "spread":     spread,
        "stride_garch": stride_garch,
        "min_confluences": config["risk"].get("min_confluences"),
        "n_bars":     n,
        "elapsed_s":  round(elapsed, 1),
        "signals":    len(signals_log),
        "metrics":    metrics,
        "discards":   discards,
        "connector_stats": hc.stats,
    }
    if not quiet:
        print(f"\n{n} velas en {elapsed/60:.1f} min | señales: {len(signals_log)} "
              f"| trades ejecutados: {len(trades)}")
        if trades:
            m = metrics
            print(f"WR {m['win_rate']:.1%} | PF {m['profit_factor']} | "
                  f"ret {m['total_return_pct']}% | DD {m['max_drawdown']:.1%} | "
                  f"avgR {m['avg_r_per_trade']}")
        if discards:
            top = sorted(discards.items(), key=lambda x: -x[1])[:12]
            print("Descartes:", *[f"  {v:5d}  {k}" for k, v in top], sep="\n")
    return out | {"signals_log": signals_log, "trades": trades, "equity": equity}


def save_results(res: dict, tag: str = "") -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS_DIR, f"unified_{tag or 'run'}_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, ensure_ascii=False, default=str)
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--from", dest="t_from", default=None, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--to", dest="t_to", default=None, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--min-confluences", type=float, default=None)
    ap.add_argument("--candidates", action="store_true",
                    help="modo candidatos (registra todo; usa --min-confluences 0)")
    ap.add_argument("--spread", type=float, default=0.30)
    ap.add_argument("--stride-garch", type=int, default=1)
    ap.add_argument("--discards", action="store_true", help="contar motivos de descarte")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    res = run_unified(symbol=args.symbol, t_from=args.t_from, t_to=args.t_to,
                      days=args.days, min_confluences=args.min_confluences,
                      candidates=args.candidates, spread=args.spread,
                      stride_garch=args.stride_garch, record_discards=args.discards)
    path = save_results(res, args.tag)
    print(f"Resultados → {path}")

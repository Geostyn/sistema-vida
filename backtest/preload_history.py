"""
preload_history — Descarga el histórico para el backtester unificado (1 vez).

Con MT5 ABIERTO (y el bot parado para no competir por la API):
    python backtest/preload_history.py                 # descarga desde 2023-01-01
    python backtest/preload_history.py --from 2024-01-01
    python backtest/preload_history.py --selftest      # valida el caché (SIN MT5)

Guarda en backtest/cache/:
  - {SYMBOL}_{TF}.pkl  — DataFrame time/open/high/low/close/volume (hora servidor, naive)
  - meta.json          — offset servidor↔UTC, cobertura, symbol_info, cuenta, spread real

Después de esto, el backtester unificado corre 100% de disco (sin MT5, bot vivo OK).
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.historical_connector import (
    CACHE_DIR, PRELOAD_SERIES, TF_MINUTES, HistoricalConnector, LookaheadError,
)

# Trocear descargas largas para no chocar con "Max bars in chart" del terminal
CHUNK_DAYS = {"M15": 120, "H1": 365, "H4": 730, "D1": 1500}


def _download_series(mt5, symbol: str, tf_name: str, t_from: datetime) -> pd.DataFrame:
    TF_MAP = {"M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1,
              "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1}
    tf = TF_MAP[tf_name]
    if t_from.tzinfo is None:
        t_from = t_from.replace(tzinfo=timezone.utc)
    chunks, cursor = [], t_from
    end = datetime.now(timezone.utc) + timedelta(days=2)
    step = timedelta(days=CHUNK_DAYS[tf_name])
    while cursor < end:
        c_to = min(cursor + step, end)
        rates = mt5.copy_rates_range(symbol, tf, cursor, c_to)
        if rates is not None and len(rates):
            chunks.append(pd.DataFrame(rates))
        cursor = c_to
    if not chunks:
        return pd.DataFrame()
    df = pd.concat(chunks, ignore_index=True)
    df = df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"], unit="s")  # hora SERVIDOR, naive (como MT5Connector)
    df = df[["time", "open", "high", "low", "close", "tick_volume"]].copy()
    df.rename(columns={"tick_volume": "volume"}, inplace=True)
    return df


def preload(t_from: datetime, refresh: bool = False) -> None:
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"ERROR: mt5.initialize() falló: {mt5.last_error()}")
        sys.exit(1)

    os.makedirs(CACHE_DIR, exist_ok=True)
    meta = {"created_at": datetime.now(timezone.utc).isoformat(),
            "from": t_from.isoformat(), "symbol_info": {}, "coverage": {}}

    # Offset servidor↔UTC (caveat: DST puede moverlo ±1h). Se mide con la vela
    # H1 EN FORMACIÓN: su open == floor(ahora_servidor a la hora). Los epochs de
    # MT5 codifican la hora de pared del servidor → leerlos como UTC-naive.
    mt5.symbol_select("XAUUSD", True)
    rates = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_H1, 0, 1)
    if rates is not None and len(rates):
        bar_open = datetime.fromtimestamp(int(rates[0]["time"]), tz=timezone.utc) \
            .replace(tzinfo=None)
        utc_floor = datetime.now(timezone.utc).replace(
            tzinfo=None, minute=0, second=0, microsecond=0)
        offset = round((bar_open - utc_floor).total_seconds() / 3600)
        if abs(offset) > 12:
            print(f"AVISO: offset medido {offset:+d} h implausible (¿mercado cerrado?) — revisa")
        meta["server_utc_offset_hours"] = offset
        print(f"Offset servidor-UTC: {offset:+d} h (vela H1 {bar_open} vs UTC {utc_floor})")
    tick = mt5.symbol_info_tick("XAUUSD")
    if tick:
        meta["spread_xauusd_observed"] = round(tick.ask - tick.bid, 2)
        print(f"Spread XAUUSD observado: {meta['spread_xauusd_observed']}")

    acct = mt5.account_info()
    if acct:
        meta["account"] = {"login": acct.login, "balance": acct.balance,
                           "currency": acct.currency}

    for symbol, tfs in PRELOAD_SERIES.items():
        info = mt5.symbol_info(symbol)
        if info is None:
            print(f"  {symbol}: NO disponible en el broker — se omite")
            continue
        mt5.symbol_select(symbol, True)  # asegurar en MarketWatch para históricos
        meta["symbol_info"][symbol] = {
            "symbol": symbol, "digits": info.digits, "point": info.point,
            "trade_contract_size": info.trade_contract_size,
            "volume_min": info.volume_min, "volume_max": info.volume_max,
            "volume_step": info.volume_step,
        }
        for tf in tfs:
            path = os.path.join(CACHE_DIR, f"{symbol}_{tf}.pkl")
            if os.path.exists(path) and not refresh:
                df = pd.read_pickle(path)
                print(f"  {symbol} {tf}: caché existente ({len(df)} barras) — usa --refresh para rebajar")
            else:
                df = _download_series(mt5, symbol, tf, t_from)
                if df.empty:
                    print(f"  {symbol} {tf}: SIN DATOS")
                    continue
                df.to_pickle(path)
                print(f"  {symbol} {tf}: {len(df)} barras "
                      f"({df['time'].iloc[0]} → {df['time'].iloc[-1]})")
            meta["coverage"][f"{symbol}_{tf}"] = {
                "first": str(df["time"].iloc[0]), "last": str(df["time"].iloc[-1]),
                "bars": int(len(df)),
            }

    with open(os.path.join(CACHE_DIR, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    mt5.shutdown()
    print(f"\nPreload completo → {CACHE_DIR}")


# ── Selftest anti-lookahead (corre SIN MT5, solo caché) ─────────────

def selftest() -> bool:
    hc = HistoricalConnector()
    cov = hc.coverage()
    missing = [k for k, v in cov.items() if v is None]
    print("Cobertura del caché:")
    for k, v in cov.items():
        print(f"  {k}: {v['first']} → {v['last']} ({v['bars']} barras)" if v else f"  {k}: FALTA")
    if missing:
        print(f"\nFALTAN series: {missing}")
        return False

    h1 = pd.read_pickle(os.path.join(CACHE_DIR, "XAUUSD_H1.pkl"))
    rng = np.random.default_rng(42)
    idxs = rng.integers(500, len(h1) - 5, size=25)
    ok = True

    for i in idxs:
        cursor = pd.Timestamp(h1["time"].iloc[i]) + pd.Timedelta(hours=1)  # cierre de la barra i
        hc.set_now(cursor)

        # 1. Ninguna serie devuelve barras que abran tras el cursor
        for sym, tfs in PRELOAD_SERIES.items():
            for tf in tfs:
                df = hc.get_rates(sym, tf, 300)
                if len(df) and pd.Timestamp(df["time"].iloc[-1]) > cursor:
                    print(f"  LOOKAHEAD {sym} {tf} @ {cursor}")
                    ok = False

        # 2. La última H1 devuelta ES la barra i (recién cerrada)
        df = hc.get_rates("XAUUSD", "H1", 300)
        if pd.Timestamp(df["time"].iloc[-1]) != pd.Timestamp(h1["time"].iloc[i]):
            print(f"  H1 última barra != barra recién cerrada @ {cursor}")
            ok = False

        # 3. Determinismo: dos llamadas idénticas
        df2 = hc.get_rates("XAUUSD", "H1", 300)
        if not df.equals(df2):
            print(f"  NO determinista @ {cursor}")
            ok = False

        # 4. H4 en formación == agregado de H1 cerradas del período
        df_h4 = hc.get_rates("XAUUSD", "H4", 50)
        if len(df_h4):
            last = df_h4.iloc[-1]
            p0 = pd.Timestamp(last["time"])
            if p0 + pd.Timedelta(hours=4) > cursor:  # está en formación
                sub = h1[(h1["time"] >= p0) & (h1["time"] + pd.Timedelta(hours=1) <= cursor)]
                if len(sub):
                    if (abs(float(last["high"]) - float(sub["high"].max())) > 1e-9 or
                            abs(float(last["close"]) - float(sub["close"].iloc[-1])) > 1e-9):
                        print(f"  H4 en formación mal sintetizada @ {cursor}")
                        ok = False

        # 5. get_current_price coherente (bid = último cierre M15 <= cursor)
        px = hc.get_current_price("XAUUSD")
        if px and pd.Timestamp(px["time"]) > cursor:
            print(f"  Precio con lookahead @ {cursor}")
            ok = False

    # 6. Cursor sin fijar → error explícito
    hc2 = HistoricalConnector()
    try:
        hc2.get_rates("XAUUSD", "H1", 10)
        print("  FALTA el guard de cursor sin fijar")
        ok = False
    except LookaheadError:
        pass

    print(f"\nSelftest anti-lookahead: {'OK (25 cursores x 11 series)' if ok else 'FALLOS — revisar'}")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="t_from", default="2023-01-01",
                    help="Fecha inicial de descarga (YYYY-MM-DD)")
    ap.add_argument("--refresh", action="store_true", help="Re-descargar aunque exista caché")
    ap.add_argument("--selftest", action="store_true", help="Validar caché (sin MT5)")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(0 if selftest() else 1)
    preload(datetime.fromisoformat(args.t_from), refresh=args.refresh)

"""
HistoricalConnector — duck-type de MT5Connector sobre histórico en disco.

Permite correr el SignalEngine REAL barra a barra sin MT5: expone la misma
API de datos (get_rates, get_current_price, get_symbol_info, copy_ticks_range)
pero sirviendo slices point-in-time de un caché en disco (backtest/cache/),
gobernados por un cursor temporal `set_now(t)`.

Diseño (PLAN-FABLE Fase B):
  - Tiempos en hora del SERVIDOR MT5, naive — idéntico a MT5Connector.get_rates.
    El offset servidor↔UTC medido en el preload vive en cache/meta.json.
  - Barras CERRADAS al cursor: close_time (= open + timeframe) <= now.
  - H4/D1 EN FORMACIÓN se sintetizan desde H1 cerradas del período actual —
    sin esto el bias H4 divergiría del vivo 3 de cada 4 horas.
  - bid/ask sintético: OHLC de MT5 son precios BID → bid = close,
    ask = close + spread configurable (XAUUSD ~0.30).
  - Anti-lookahead: asserts internos → LookaheadError si una barra devuelta
    abriese después del cursor. `stats` cuenta requests para auditoría.

El preload (backtest/preload_history.py) descarga las series 1 sola vez con
MT5 abierto; después todo corre de disco, sin MT5 y sin tocar el bot vivo.
"""

import os
import json
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}

# Símbolos que el preload descarga (XAUUSD + cesta DXY sintético)
PRELOAD_SERIES = {
    "XAUUSD": ["M15", "H1", "H4", "D1"],
    "EURUSD": ["H1", "H4"],
    "USDJPY": ["H1", "H4"],
    "GBPUSD": ["H1", "H4"],
    "USDCAD": ["H1"],
    "USDSEK": ["H1"],
    "USDCHF": ["H1"],
}


class LookaheadError(RuntimeError):
    """El conector iba a servir datos posteriores al cursor — bug interno."""


class HistoricalConnector:
    """Reemplazo de MT5Connector para el backtester unificado."""

    def __init__(self, spread_xauusd: float = 0.30, cache_dir: str = CACHE_DIR):
        self.cache_dir = cache_dir
        self.connected = True
        self.config = {}
        self._spread = {"XAUUSD": float(spread_xauusd)}
        self._series: dict = {}      # (symbol, tf) → {"df", "open_ns", "close_ns"}
        self._now: pd.Timestamp | None = None
        self.stats = {"requests": 0, "forming_bars": 0}
        self.meta = {}
        meta_path = os.path.join(cache_dir, "meta.json")
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                self.meta = json.load(f)

    # ── Cursor temporal ─────────────────────────────────────────────

    def set_now(self, t) -> None:
        """Fija el 'ahora' del backtest (hora SERVIDOR, naive)."""
        self._now = pd.Timestamp(t)
        if self._now.tzinfo is not None:
            raise ValueError("set_now espera hora de servidor NAIVE (sin tz)")

    def now(self) -> pd.Timestamp | None:
        return self._now

    def utc_offset_hours(self) -> float:
        """Offset servidor−UTC medido en el preload (caveat DST ±1h)."""
        return float(self.meta.get("server_utc_offset_hours", 0.0))

    # ── Carga de series (1 vez, luego en memoria) ───────────────────

    def _load(self, symbol: str, timeframe: str):
        key = (symbol, timeframe)
        if key in self._series:
            return self._series[key]
        path = os.path.join(self.cache_dir, f"{symbol}_{timeframe}.pkl")
        if not os.path.exists(path):
            self._series[key] = None
            return None
        df = pd.read_pickle(path)
        open_ns = df["time"].values.astype("datetime64[ns]")
        close_ns = (df["time"] + pd.Timedelta(minutes=TF_MINUTES[timeframe])) \
            .values.astype("datetime64[ns]")
        entry = {"df": df, "open_ns": open_ns, "close_ns": close_ns}
        self._series[key] = entry
        return entry

    def _closed_upto(self, symbol: str, timeframe: str):
        """(df, idx): df.iloc[:idx] son las barras CERRADAS al cursor. O(log n)."""
        if self._now is None:
            raise LookaheadError("Cursor sin fijar — llama set_now() antes de pedir datos")
        entry = self._load(symbol, timeframe)
        if entry is None:
            return None
        idx = int(np.searchsorted(entry["close_ns"], np.datetime64(self._now), side="right"))
        return entry["df"], idx

    # ── Barra HTF en formación (desde H1 cerradas) ──────────────────

    # Sub-timeframe con el que se sintetiza la vela en formación de cada TF
    _FORMING_SUB = {"H4": "H1", "D1": "H1", "H1": "M15"}

    def _forming_bar(self, symbol: str, timeframe: str, last_closed_open) -> pd.DataFrame | None:
        mins = TF_MINUTES[timeframe]
        if timeframe == "D1":
            period_start = self._now.floor("D")
        else:
            period_start = self._now.floor(f"{mins}min")
        # Si el período en formación ya está cubierto por la última cerrada
        # (cursor justo en el límite) no hay nada que sintetizar
        if last_closed_open is not None and pd.Timestamp(last_closed_open) >= period_start:
            return None
        sub_tf = self._FORMING_SUB.get(timeframe)
        if sub_tf is None:
            return None
        base = self._closed_upto(symbol, sub_tf)
        if base is None:
            return None
        sub_df, idx = base
        if idx == 0:
            return None
        j0 = int(np.searchsorted(sub_df["time"].values.astype("datetime64[ns]")[:idx],
                                 np.datetime64(period_start), side="left"))
        sub = sub_df.iloc[j0:idx]
        if sub.empty:
            return None
        self.stats["forming_bars"] += 1
        return pd.DataFrame([{
            "time":   period_start,
            "open":   float(sub["open"].iloc[0]),
            "high":   float(sub["high"].max()),
            "low":    float(sub["low"].min()),
            "close":  float(sub["close"].iloc[-1]),
            "volume": float(sub["volume"].sum()),
        }])

    # ── API duck-type de MT5Connector ───────────────────────────────

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass

    def _ensure_connected(self) -> None:
        pass

    def test_connection(self) -> dict:
        acct = self.meta.get("account", {})
        return {"ok": True, "login": acct.get("login", 0), "server": "HISTORICAL",
                "balance": acct.get("balance", 100000.0),
                "equity": acct.get("balance", 100000.0),
                "currency": acct.get("currency", "EUR"), "leverage": 100}

    def get_rates(self, symbol: str, timeframe: str, n_bars: int = 500) -> pd.DataFrame:
        self.stats["requests"] += 1
        if timeframe not in TF_MINUTES:
            return pd.DataFrame()  # W1 etc.: solo lo usa swing (desactivado)
        base = self._closed_upto(symbol, timeframe)
        if base is None:
            return pd.DataFrame()
        df, idx = base
        closed = df.iloc[max(0, idx - n_bars):idx]
        out = closed
        # HTF siempre; H1 solo si el cursor va a cadencia sub-horaria (en un
        # cierre H1 exacto el período en formación está vacío → no-op)
        if timeframe in ("H4", "D1", "H1"):
            last_open = closed["time"].iloc[-1] if len(closed) else None
            forming = self._forming_bar(symbol, timeframe, last_open)
            if forming is not None:
                out = pd.concat([closed, forming], ignore_index=True).tail(n_bars)
        out = out.copy().reset_index(drop=True)
        # Self-check anti-lookahead: ninguna barra devuelta abre tras el cursor
        if len(out) and pd.Timestamp(out["time"].iloc[-1]) > self._now:
            raise LookaheadError(
                f"{symbol} {timeframe}: barra {out['time'].iloc[-1]} > cursor {self._now}")
        return out

    def get_rates_range(self, symbol: str, timeframe: str,
                        date_from, date_to) -> pd.DataFrame:
        """Rango [from, to] CAPADO al cursor (anti-lookahead)."""
        base = self._closed_upto(symbol, timeframe)
        if base is None:
            return pd.DataFrame()
        df, idx = base
        sub = df.iloc[:idx]
        mask = (sub["time"] >= pd.Timestamp(date_from)) & (sub["time"] <= pd.Timestamp(date_to))
        return sub.loc[mask].copy().reset_index(drop=True)

    def get_current_price(self, symbol: str) -> dict:
        """Precio sintético al cursor: último cierre M15 (si hay) o H1.
        OHLC de MT5 = BID → bid = close, ask = close + spread."""
        for tf in ("M15", "H1"):
            base = self._closed_upto(symbol, tf)
            if base is None:
                continue
            df, idx = base
            if idx == 0:
                continue
            close = float(df["close"].iloc[idx - 1])
            spread = self._spread.get(symbol, 0.0)
            digits = int(self.get_symbol_info(symbol).get("digits", 5))
            return {
                "symbol": symbol,
                "bid":    close,
                "ask":    round(close + spread, digits),
                "spread": spread,
                "time":   pd.Timestamp(df["time"].iloc[idx - 1]).to_pydatetime(),
            }
        return {}

    def get_symbol_info(self, symbol: str) -> dict:
        info = (self.meta.get("symbol_info") or {}).get(symbol)
        if info:
            return dict(info)
        # Fallback razonable si el preload no capturó el símbolo
        defaults = {"XAUUSD": {"digits": 2, "point": 0.01, "trade_contract_size": 100.0}}
        base = defaults.get(symbol, {"digits": 5, "point": 0.00001,
                                     "trade_contract_size": 100000.0})
        return {"symbol": symbol, **base,
                "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01}

    def get_account_info(self) -> dict:
        acct = self.meta.get("account", {})
        bal = acct.get("balance", 100000.0)
        return {"balance": bal, "equity": bal, "margin": 0.0,
                "free_margin": bal, "profit": 0.0,
                "currency": acct.get("currency", "EUR")}

    def copy_ticks_range(self, symbol: str, date_from, date_to, flags=None):
        """Sin ticks en histórico v1 → delta/footprint queda neutro."""
        return None

    def get_positions(self, magic: int = None) -> list:
        return []

    def get_pending_orders(self, magic: int = None) -> list:
        return []

    # ── Utilidades para el backtester ───────────────────────────────

    def iter_closes(self, symbol: str, timeframe: str, t_from, t_to):
        """Genera los instantes de CIERRE de cada vela del TF en [t_from, t_to]
        (hora servidor). El bucle del backtester fija el cursor en cada uno."""
        entry = self._load(symbol, timeframe)
        if entry is None:
            return
        closes = entry["df"]["time"] + pd.Timedelta(minutes=TF_MINUTES[timeframe])
        mask = (closes >= pd.Timestamp(t_from)) & (closes <= pd.Timestamp(t_to))
        for t in closes[mask]:
            yield pd.Timestamp(t)

    def iter_h1_closes(self, symbol: str, t_from, t_to):
        yield from self.iter_closes(symbol, "H1", t_from, t_to)

    def coverage(self) -> dict:
        """Cobertura real de cada serie del caché (para preflight)."""
        out = {}
        for sym, tfs in PRELOAD_SERIES.items():
            for tf in tfs:
                entry = self._load(sym, tf)
                if entry is None or entry["df"].empty:
                    out[f"{sym}_{tf}"] = None
                else:
                    df = entry["df"]
                    out[f"{sym}_{tf}"] = {
                        "first": str(df["time"].iloc[0]),
                        "last":  str(df["time"].iloc[-1]),
                        "bars":  int(len(df)),
                    }
        return out

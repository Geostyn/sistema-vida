"""
yf_safe — Lectura BLINDADA de Yahoo Finance (yfinance).

Motivo: los módulos macro/VP/intermarket llamaban a
    yf.Ticker(tk, session=SESS).history(period=..., interval=...)
sin timeout ni reintento. Ante un rate-limit (429/503) o un corte de red,
yfinance devuelve un DataFrame vacío EN SILENCIO → el bot operaba con datos
obsoletos o perdía la confluencia sin avisar.

`safe_history()` centraliza:
  - timeout explícito por llamada,
  - reintentos con backoff exponencial,
  - detección de "vacío" (síntoma típico de rate-limit) → reintenta,
  - degradación limpia: si todo falla devuelve un DataFrame vacío (los
    llamadores ya saben caer a su caché), y lo deja registrado en el log.

Compatibilidad: algunas versiones de yfinance no aceptan `timeout=` en
`history()`; se reintenta sin él si da TypeError.
"""

from __future__ import annotations

import time
import logging
import pandas as pd

logger = logging.getLogger(__name__)


def safe_history(ticker: str, session=None, retries: int = 2,
                 timeout: int = 8, backoff: float = 0.6, **kwargs) -> pd.DataFrame:
    """Descarga OHLCV de yfinance con timeout + reintentos. Nunca lanza."""
    import yfinance as yf

    last_err = None
    for attempt in range(retries + 1):
        try:
            tk = (yf.Ticker(ticker, session=session)
                  if session is not None else yf.Ticker(ticker))
            try:
                df = tk.history(timeout=timeout, **kwargs)
            except TypeError:
                # Versión de yfinance sin parámetro timeout en history()
                df = tk.history(**kwargs)
            if df is not None and not df.empty:
                return df
            last_err = "DataFrame vacío (posible rate-limit de Yahoo)"
        except Exception as e:  # red, SSL, parseo…
            last_err = e
            # Reintentar un 429 es CONTRAPRODUCENTE (más carga → prolonga el
            # baneo). Ante rate-limit, fallar rápido y dejar que la caché cubra.
            if "too many requests" in str(e).lower() or "rate limit" in str(e).lower():
                break
        if attempt < retries:
            time.sleep(backoff * (2 ** attempt))  # 0.6s, 1.2s, 2.4s…

    logger.warning(
        f"[yf_safe] {ticker} sin datos tras {retries + 1} intento(s): {last_err}"
    )
    return pd.DataFrame()

"""
neutral_mocks — Mocks NEUTROS constantes para el backtester unificado.

Las noticias y el macro global no se pueden reconstruir punto a punto en el
histórico. En vez de fingirlas, se fijan en su estado NEUTRO: aportan una
constante a TODAS las señales por igual → no distorsionan el ranking entre
señales ni los barridos de umbral (solo desplazan la escala, medida en el
replay contra trades.db como parte del gap Δ̄).

  - NewsNeutralMock  → sin blackout → +1.0 confluencia ("Sin noticias") fija
  - MacroNeutralMock → gold_bias NEUTRAL → +0.3 confluencias fijas
"""


class NewsNeutralMock:
    """Duck-type de data.news_feed.NewsFeed para SignalEngine."""

    def is_news_blackout(self, minutes_buffer: int = 30) -> dict:
        return {"blackout": False, "reason": "", "event": None}

    def get_daily_summary(self) -> list:
        return []


class MacroNeutralMock:
    """Duck-type de data.macro_feed.MacroFeed para SignalEngine."""

    def get_macro_bias(self) -> dict:
        return {
            "gold_bias": "NEUTRAL",
            "score":     0.0,
            "components": {},
            "source":    "mock-neutral (backtest unificado)",
        }

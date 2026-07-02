"""CandleSource adapter interface.

Product path: TradingViewMcpCandleSource (TradingView Desktop via CDP MCP bridge).
Test path:    FixtureCandleSource (CSV fixtures) — tests/offline development ONLY.

There is intentionally no public-endpoint fallback (no Yahoo, no yfinance).
If the product source cannot deliver, it raises a typed DataSourceError.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Candle


class CandleSource(ABC):
    """A source of OHLCV bars for a (symbol, timeframe)."""

    name: str = "abstract"

    @abstractmethod
    def get_candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]:
        """Return up to `count` most recent CLOSED bars, ascending by ts.

        Implementations MUST:
        - return bars for exactly the requested symbol/timeframe or raise SymbolMismatch
        - never include a still-forming bar when the source can know better
        - raise a DataSourceError subclass on failure; never return wrong-but-plausible data
        """

    @abstractmethod
    def health_check(self) -> dict:
        """Return a dict describing source health; raise DataSourceError if unusable."""

    def close(self) -> None:  # pragma: no cover - optional
        pass

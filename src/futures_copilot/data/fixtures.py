"""FixtureCandleSource — CSV-backed bars for tests and offline development ONLY.

This is not a product data path. The collector refuses to run against fixtures
unless explicitly told it is in fixture mode, and bars are tagged source='fixture'
so they can never masquerade as live data.

CSV format (header required):
    symbol,timeframe,ts,open,high,low,close,volume
ts = bar open time, UTC epoch seconds.
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..errors import FixtureError, NoBarsReturned, SymbolMismatch
from ..models import Candle
from .base import CandleSource

REQUIRED_COLS = {"symbol", "timeframe", "ts", "open", "high", "low", "close", "volume"}


def load_fixture_csv(path: str | Path) -> list[Candle]:
    path = Path(path)
    if not path.exists():
        raise FixtureError(f"fixture file not found: {path}")
    candles: list[Candle] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or not REQUIRED_COLS.issubset(set(reader.fieldnames)):
            raise FixtureError(
                f"fixture {path} missing columns; need {sorted(REQUIRED_COLS)}, got {reader.fieldnames}"
            )
        for i, row in enumerate(reader):
            try:
                candles.append(
                    Candle(
                        symbol=row["symbol"],
                        timeframe=row["timeframe"],
                        ts=int(row["ts"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        source="fixture",
                    )
                )
            except (ValueError, KeyError) as e:
                raise FixtureError(f"bad fixture row {i + 2} in {path}: {e}") from e
    candles.sort(key=lambda c: c.ts)
    return candles


class FixtureCandleSource(CandleSource):
    name = "fixture"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._all = load_fixture_csv(self.path)

    def get_candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]:
        matched = [c for c in self._all if c.symbol == symbol and c.timeframe == timeframe]
        if not matched:
            available = sorted({(c.symbol, c.timeframe) for c in self._all})
            raise SymbolMismatch(
                f"fixture {self.path} has no bars for ({symbol}, {timeframe}); available: {available}"
            )
        out = matched[-count:]
        if not out:
            raise NoBarsReturned(f"fixture returned zero bars for ({symbol}, {timeframe})")
        return out

    def health_check(self) -> dict:
        return {
            "source": self.name,
            "path": str(self.path),
            "bars_loaded": len(self._all),
            "warning": "FIXTURE MODE — not live market data",
        }

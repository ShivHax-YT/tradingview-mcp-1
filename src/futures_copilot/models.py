"""Core data models."""

from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator


class Candle(BaseModel):
    """One OHLCV bar. `ts` is the bar OPEN time as UTC epoch seconds."""

    symbol: str
    timeframe: str
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    source: str = "unknown"  # tvmcp | resampled | fixture

    @field_validator("ts")
    @classmethod
    def _sane_epoch(cls, v: int) -> int:
        # Reject millisecond epochs and obviously bogus values.
        if v > 10_000_000_000:
            raise ValueError(f"ts must be epoch SECONDS, got {v} (looks like milliseconds)")
        if v < 946_684_800:  # 2000-01-01
            raise ValueError(f"ts implausibly old: {v}")
        return v

    @model_validator(mode="after")
    def _ohlc_sane(self) -> "Candle":
        if self.high < self.low:
            raise ValueError(f"high {self.high} < low {self.low}")
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError(
                f"open/close outside high-low range: o={self.open} h={self.high} l={self.low} c={self.close}"
            )
        return self

    def as_row(self) -> tuple:
        return (
            self.symbol, self.timeframe, self.ts,
            self.open, self.high, self.low, self.close, self.volume, self.source,
        )

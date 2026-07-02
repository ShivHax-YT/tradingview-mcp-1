"""Resample 1m bars to higher timeframes.

Rules:
- 1m is the canonical collected timeframe; 3m/5m/15m/1h/4h derive from it.
- Bar label = window OPEN time (left edge), matching TradingView convention.
- A derived bar is emitted ONLY if its window is complete (all constituent
  1m bars present). Incomplete windows are dropped — a partially-built 15m
  bar is worse than no bar, because features computed on it look plausible
  and are wrong. CME maintenance gaps therefore simply produce no bar.
"""

from __future__ import annotations

import pandas as pd

from ..errors import UnsupportedTimeframe
from ..models import Candle

TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}

_PANDAS_FREQ = {"3m": "3min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h"}


def tf_seconds(timeframe: str) -> int:
    try:
        return TF_SECONDS[timeframe]
    except KeyError:
        raise UnsupportedTimeframe(
            f"unknown timeframe {timeframe!r}; supported: {sorted(TF_SECONDS)}"
        ) from None


def resample_1m(candles_1m: list[Candle], target_tf: str) -> list[Candle]:
    """Aggregate 1m bars into `target_tf` bars. Only complete windows are returned."""
    if target_tf not in _PANDAS_FREQ:
        raise UnsupportedTimeframe(
            f"cannot resample to {target_tf!r}; supported targets: {sorted(_PANDAS_FREQ)}"
        )
    if not candles_1m:
        return []

    bad = [c for c in candles_1m if c.timeframe != "1m"]
    if bad:
        raise UnsupportedTimeframe(
            f"resample input must be 1m bars; got timeframe {bad[0].timeframe!r}"
        )

    symbol = candles_1m[0].symbol
    df = pd.DataFrame(
        {
            "open": [c.open for c in candles_1m],
            "high": [c.high for c in candles_1m],
            "low": [c.low for c in candles_1m],
            "close": [c.close for c in candles_1m],
            "volume": [c.volume for c in candles_1m],
        },
        index=pd.to_datetime([c.ts for c in candles_1m], unit="s", utc=True),
    ).sort_index()
    # Dedupe defensively (store should prevent this, but never aggregate twice).
    df = df[~df.index.duplicated(keep="last")]

    expected = tf_seconds(target_tf) // 60
    agg = df.resample(_PANDAS_FREQ[target_tf], label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        n=("close", "count"),
    )
    complete = agg[agg["n"] == expected]

    return [
        Candle(
            symbol=symbol,
            timeframe=target_tf,
            ts=int(idx.timestamp()),
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=float(r["volume"]),
            source="resampled",
        )
        for idx, r in complete.iterrows()
    ]

"""Helpers to build hand-crafted candle DataFrames for feature tests."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")


def et_ts(y: int, mo: int, d: int, h: int, mi: int) -> int:
    """Epoch seconds for a wall-clock America/New_York moment."""
    return int(datetime(y, mo, d, h, mi, tzinfo=ET).timestamp())


def make_df(rows: list[tuple]) -> pd.DataFrame:
    """rows: (ts, open, high, low, close[, volume]) -> store-shaped DataFrame."""
    recs = []
    for r in rows:
        ts, o, h, l, c = r[:5]
        v = r[5] if len(r) > 5 else 100.0
        recs.append({"ts": int(ts), "open": float(o), "high": float(h),
                     "low": float(l), "close": float(c), "volume": float(v),
                     "source": "fixture"})
    df = pd.DataFrame(recs).sort_values("ts").reset_index(drop=True)
    df.index = pd.to_datetime(df["ts"], unit="s", utc=True)
    df.index.name = "time"
    return df


def minute_bars(start_ts: int, ohlcv: list[tuple]) -> pd.DataFrame:
    """Sequential 1m bars starting at start_ts; ohlcv items are (o,h,l,c[,v])."""
    return make_df([(start_ts + i * 60, *bar) for i, bar in enumerate(ohlcv)])

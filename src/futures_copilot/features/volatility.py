"""ATR — Average True Range, Wilder smoothing."""

from __future__ import annotations

import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ATR. First bar's TR is high-low (no prior close).

    Uses ewm(alpha=1/period, adjust=False), which is exactly Wilder's RMA.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean().rename("atr")

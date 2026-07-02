"""Anchored VWAP (volume-weighted average price).

Typical price (H+L+C)/3 weighted by volume, cumulative from an anchor.
The standard intraday anchor here is the trading-day start (18:00 ET);
day_vwap() slices and anchors for you.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from ..config import SessionsConfig
from .levels import slice_window
from .sessions import trading_day_bounds


def anchored_vwap(df: pd.DataFrame) -> pd.Series:
    """Cumulative VWAP over the given slice (anchor = first bar of the slice)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = (tp * df["volume"]).cumsum()
    v = df["volume"].cumsum()
    # Zero cumulative volume (e.g. synthetic bars): fall back to cumulative mean of typical price.
    fallback = tp.expanding().mean()
    return pd.Series(np.where(v > 0, pv / v.replace(0, np.nan), fallback), index=df.index, name="vwap")


def day_vwap(df_1m: pd.DataFrame, day: date, cfg: SessionsConfig) -> pd.Series:
    """VWAP series anchored at the trading-day start. Empty series if no bars."""
    s, e = trading_day_bounds(day, cfg)
    return anchored_vwap(slice_window(df_1m, s, e))

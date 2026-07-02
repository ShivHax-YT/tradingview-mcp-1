"""Liquidity sweep + reclaim detection.

Vocabulary (matches the strategy spec):
- SELL-side liquidity rests BELOW lows (session low, PDL, swing low).
  A sell-side sweep = price trades below the level. Bullish context.
- BUY-side liquidity rests ABOVE highs. A buy-side sweep = price trades above.
  Bearish context.
- Reclaim = a candle CLOSE back on the original side of the swept level,
  within max_candles of the sweep bar (inclusive — a wick-through bar that
  closes back across the level sweeps and reclaims in 1 candle).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

SIDES = ("sell", "buy")


@dataclass(frozen=True)
class Sweep:
    side: str          # 'sell' (below lows) | 'buy' (above highs)
    level: float
    level_name: str
    ts: int            # open time of the sweep bar
    idx: int           # integer position in the df it was detected on
    extreme: float     # how far price wicked through (low for sell, high for buy)


@dataclass(frozen=True)
class Reclaim:
    ts: int                   # open time of the reclaiming bar
    close: float
    candles_after_sweep: int  # 1 = same bar as sweep


def detect_sweep(
    df: pd.DataFrame, level: float, side: str, level_name: str = "", start_ts: int | None = None
) -> Sweep | None:
    """First bar (at/after start_ts) that trades through `level` on `side`."""
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    ts = df["ts"].to_numpy()
    lows = df["low"].to_numpy()
    highs = df["high"].to_numpy()
    for i in range(len(df)):
        if start_ts is not None and ts[i] < start_ts:
            continue
        if side == "sell" and lows[i] < level:
            return Sweep("sell", level, level_name, int(ts[i]), i, float(lows[i]))
        if side == "buy" and highs[i] > level:
            return Sweep("buy", level, level_name, int(ts[i]), i, float(highs[i]))
    return None


def detect_reclaim(df: pd.DataFrame, sweep: Sweep, max_candles: int) -> Reclaim | None:
    """First close back across the swept level within max_candles of the sweep bar.

    Must be called with the SAME df the sweep was detected on (sweep.idx is positional).
    sell-side sweep -> reclaim is close > level (bullish);
    buy-side sweep  -> reclaim is close < level (bearish).
    """
    closes = df["close"].to_numpy()
    ts = df["ts"].to_numpy()
    end = min(sweep.idx + max_candles, len(df))
    for i in range(sweep.idx, end):
        c = float(closes[i])
        if (sweep.side == "sell" and c > sweep.level) or (sweep.side == "buy" and c < sweep.level):
            return Reclaim(int(ts[i]), c, i - sweep.idx + 1)
    return None

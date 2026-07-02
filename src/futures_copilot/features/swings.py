"""Fractal swing highs/lows with explicit confirmation lag (no lookahead).

A swing high at bar i (strictly higher high than k bars on each side) is only
KNOWN once bar i+k closes. Every SwingPoint carries confirmed_close_ts — the
close time of that confirming bar — and callers must filter with
swings_confirmed_by() when deciding "what did I know at time T".
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class SwingPoint:
    kind: str                 # 'high' | 'low'
    price: float
    bar_ts: int               # open time of the swing bar
    confirmed_close_ts: int   # moment this swing became knowable


def find_swings(df: pd.DataFrame, k: int, tf_seconds: int) -> list[SwingPoint]:
    """All fractal swings in df (k bars each side, strict inequality)."""
    if k < 1:
        raise ValueError("k must be >= 1")
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    ts = df["ts"].to_numpy()
    n = len(df)
    out: list[SwingPoint] = []
    for i in range(k, n - k):
        confirmed = int(ts[i + k]) + tf_seconds
        if all(highs[i] > highs[i - j] for j in range(1, k + 1)) and all(
            highs[i] > highs[i + j] for j in range(1, k + 1)
        ):
            out.append(SwingPoint("high", float(highs[i]), int(ts[i]), confirmed))
        if all(lows[i] < lows[i - j] for j in range(1, k + 1)) and all(
            lows[i] < lows[i + j] for j in range(1, k + 1)
        ):
            out.append(SwingPoint("low", float(lows[i]), int(ts[i]), confirmed))
    out.sort(key=lambda s: s.bar_ts)
    return out


def swings_confirmed_by(swings: list[SwingPoint], as_of_ts: int) -> list[SwingPoint]:
    """Only the swings that were actually knowable at as_of_ts."""
    return [s for s in swings if s.confirmed_close_ts <= as_of_ts]

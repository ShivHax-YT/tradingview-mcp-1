"""Equal high/low ("stop magnet") detection near a proposed stop.

Resting equal lows under a long stop (or equal highs above a short stop) are
obvious liquidity: engineered sweeps love to run them before the real move.
Desk Mode v0.2 rejects candidates whose stop sits inside such a cluster.

Rules (config-driven at the call site):
- long candidates inspect bar LOWS near the stop; shorts inspect HIGHS
- tolerance is expressed in price units (ticks * tick_size upstream)
- only CLOSED bars at/before the market-state horizon are considered
  (ts + tf_seconds <= horizon_ts) — no lookahead, ever
- the scan window is the last `lookback_bars` of those closed bars
- at least 2 matching bars are required to call it an equal-level cluster
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class EqualLevelHit:
    side: str                 # 'lows' (long stop) | 'highs' (short stop)
    price: float              # mean of the matched extremes
    count: int                # matching bars (>= 2 by contract)
    bar_ts: tuple[int, ...]   # open times of the matching bars

    def describe(self) -> str:
        return (f"{self.count} bars with equal {self.side} ~{self.price:.2f} "
                f"within tolerance of the stop")


def equal_level_clusters(
    df: pd.DataFrame,
    *,
    tolerance: float,
    lookback_bars: int,
    horizon_ts: int,
    tf_seconds: int,
) -> list[EqualLevelHit]:
    """Enumerate confirmed equal-high/low clusters without lookahead.

    Cluster prices use the resting-liquidity extreme (minimum low / maximum
    high), while ``bar_ts`` preserves when the contributing bars were known.
    """
    if df is None or df.empty or tolerance < 0 or lookback_bars <= 0:
        return []
    recent = df[df["ts"] + tf_seconds <= horizon_ts].tail(lookback_bars)
    out: list[EqualLevelHit] = []
    for col, side in (("low", "lows"), ("high", "highs")):
        remaining = [(int(t), float(v)) for t, v in zip(recent["ts"], recent[col])]
        while remaining:
            anchor = remaining[0][1]
            matched = [(t, v) for t, v in remaining if abs(v - anchor) <= tolerance + 1e-9]
            remaining = [(t, v) for t, v in remaining if (t, v) not in matched]
            if len(matched) >= 2:
                price = min(v for _, v in matched) if side == "lows" else max(v for _, v in matched)
                out.append(EqualLevelHit(side, price, len(matched), tuple(t for t, _ in matched)))
    return out


def equal_level_near_stop(
    df: pd.DataFrame,
    *,
    direction: str,
    stop: float,
    tolerance: float,
    lookback_bars: int,
    horizon_ts: int,
    tf_seconds: int,
) -> EqualLevelHit | None:
    """Return a hit when >=2 recent closed bars have their low (long) / high
    (short) within `tolerance` of `stop`. None otherwise."""
    if df is None or df.empty or tolerance < 0 or lookback_bars <= 0:
        return None
    closed = df[df["ts"] + tf_seconds <= horizon_ts]
    if closed.empty:
        return None
    recent = closed.tail(lookback_bars)
    col = "low" if direction == "long" else "high"
    matched: list[tuple[int, float]] = [
        (int(t), float(v))
        for t, v in zip(recent["ts"].to_numpy(), recent[col].to_numpy())
        if abs(float(v) - stop) <= tolerance + 1e-9
    ]
    if len(matched) < 2:
        return None
    return EqualLevelHit(
        side="lows" if direction == "long" else "highs",
        price=sum(v for _, v in matched) / len(matched),
        count=len(matched),
        bar_ts=tuple(t for t, _ in matched),
    )

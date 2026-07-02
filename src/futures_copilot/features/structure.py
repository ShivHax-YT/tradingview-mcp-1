"""Market structure shift (MSS) and Change In State of Delivery (CISD).

MSS  — a candle CLOSE through the most recent *confirmed* swing (above the last
       swing high for bullish, below the last swing low for bearish). Confirmed
       means the swing's confirmation bar had closed BEFORE the breaking close —
       enforcing no-lookahead exactly like a live chart.

CISD — per the Confirmation Model guide: for longs, price closes above the OPEN
       of the earliest candle in the last consecutive series of down-close
       candles (the series that delivered into the sweep). For shorts, mirror.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .swings import SwingPoint

DIRECTIONS = ("bullish", "bearish")


@dataclass(frozen=True)
class StructureEvent:
    kind: str        # 'MSS' | 'CISD'
    direction: str   # 'bullish' | 'bearish'
    ts: int          # open time of the bar whose close triggered the event
    level: float     # the level whose break defined the event


def detect_mss(
    df: pd.DataFrame,
    swings: list[SwingPoint],
    direction: str,
    tf_seconds: int,
    after_ts: int | None = None,
) -> StructureEvent | None:
    """First close through the latest already-confirmed opposing swing."""
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}")
    want_kind = "high" if direction == "bullish" else "low"
    candidates = [s for s in swings if s.kind == want_kind]
    if not candidates:
        return None

    ts = df["ts"].to_numpy()
    closes = df["close"].to_numpy()
    for i in range(len(df)):
        bar_open_ts = int(ts[i])
        if after_ts is not None and bar_open_ts < after_ts:
            continue
        bar_close_ts = bar_open_ts + tf_seconds
        # Latest swing that (a) formed before this bar and (b) was confirmed by the
        # time this bar closed. Only that swing was actually on the chart.
        known = [
            s for s in candidates
            if s.confirmed_close_ts <= bar_close_ts and s.bar_ts < bar_open_ts
        ]
        if not known:
            continue
        ref = known[-1]
        c = float(closes[i])
        if direction == "bullish" and c > ref.price:
            return StructureEvent("MSS", "bullish", bar_open_ts, ref.price)
        if direction == "bearish" and c < ref.price:
            return StructureEvent("MSS", "bearish", bar_open_ts, ref.price)
    return None


def detect_cisd(
    df: pd.DataFrame,
    direction: str,
    anchor_idx: int,
    max_series_len: int = 10,
    max_candles_after: int = 20,
) -> StructureEvent | None:
    """CISD relative to an anchor bar (typically the sweep bar / extreme).

    bullish: find the last consecutive run of down-close candles ending at or
    before anchor_idx; threshold = OPEN of the first candle of that run; the
    event fires on the first later close above the threshold.
    """
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}")
    if not (0 <= anchor_idx < len(df)):
        raise ValueError(f"anchor_idx {anchor_idx} out of range")

    opens = df["open"].to_numpy()
    closes = df["close"].to_numpy()
    ts = df["ts"].to_numpy()

    def is_series_bar(i: int) -> bool:
        return closes[i] < opens[i] if direction == "bullish" else closes[i] > opens[i]

    # Walk back to the end of the delivery series (skip a reversal bar sitting at the anchor).
    end = anchor_idx
    while end >= 0 and not is_series_bar(end):
        end -= 1
    if end < 0:
        return None
    # Walk to the start of the consecutive series.
    start = end
    while start - 1 >= 0 and is_series_bar(start - 1) and (end - start + 1) < max_series_len:
        start -= 1
    threshold = float(opens[start])

    stop = min(end + 1 + max_candles_after, len(df))
    for i in range(end + 1, stop):
        c = float(closes[i])
        if direction == "bullish" and c > threshold:
            return StructureEvent("CISD", "bullish", int(ts[i]), threshold)
        if direction == "bearish" and c < threshold:
            return StructureEvent("CISD", "bearish", int(ts[i]), threshold)
    return None

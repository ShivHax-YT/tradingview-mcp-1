"""Fair Value Gaps (FVG) and inversions (iFVG), with full lifecycle tracking.

Definition (3-candle): bullish FVG when low[i+2] > high[i] — the zone between
them was skipped. Bearish mirrored. The gap only EXISTS once bar i+2 closes
(created_close_ts), and its status evolves:

  open       -> untouched
  mitigated  -> price traded back into the zone
  filled     -> price traversed the whole zone
  inverted   -> a candle CLOSED through the far side; the gap flips role
                (bullish FVG -> bearish iFVG resistance and vice versa)

Knowledge-horizon rule: this engine only ever sees CLOSED bars, so every
lifecycle timestamp is the CLOSE time of the bar that caused the event.
status(as_of_ts) therefore never reports something a closed-candle system
could not yet have known.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class FVG:
    kind: str                # 'bullish' | 'bearish'
    top: float
    bottom: float
    created_bar_ts: int      # open time of the third bar
    created_close_ts: int    # when the gap became knowable
    mitigated_ts: int | None = None   # close time of the first bar touching the zone
    filled_ts: int | None = None      # close time of the first bar traversing the zone
    inverted_ts: int | None = None    # close time of the bar that closed through

    def status(self, as_of_ts: int) -> str:
        if self.created_close_ts > as_of_ts:
            return "nonexistent"
        if self.inverted_ts is not None and self.inverted_ts <= as_of_ts:
            return "inverted"
        if self.filled_ts is not None and self.filled_ts <= as_of_ts:
            return "filled"
        if self.mitigated_ts is not None and self.mitigated_ts <= as_of_ts:
            return "mitigated"
        return "open"

    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0


def detect_fvgs(df: pd.DataFrame, tf_seconds: int, min_size: float = 0.0) -> list[FVG]:
    """All FVGs in df with lifecycle timestamps resolved against the same df."""
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    ts = df["ts"].to_numpy()
    n = len(df)
    out: list[FVG] = []

    for i in range(n - 2):
        third = i + 2
        if lows[third] > highs[i] and (lows[third] - highs[i]) >= min_size:
            g = FVG(
                kind="bullish", top=float(lows[third]), bottom=float(highs[i]),
                created_bar_ts=int(ts[third]), created_close_ts=int(ts[third]) + tf_seconds,
            )
        elif highs[third] < lows[i] and (lows[i] - highs[third]) >= min_size:
            g = FVG(
                kind="bearish", top=float(lows[i]), bottom=float(highs[third]),
                created_bar_ts=int(ts[third]), created_close_ts=int(ts[third]) + tf_seconds,
            )
        else:
            continue

        # Lifecycle: scan bars after the third bar. Events stamp the CLOSE of their bar.
        for j in range(third + 1, n):
            bar_close_ts = int(ts[j]) + tf_seconds
            if g.kind == "bullish":
                touched = lows[j] <= g.top
                traversed = lows[j] <= g.bottom
                closed_through = closes[j] < g.bottom
            else:
                touched = highs[j] >= g.bottom
                traversed = highs[j] >= g.top
                closed_through = closes[j] > g.top
            if touched and g.mitigated_ts is None:
                g.mitigated_ts = bar_close_ts
            if traversed and g.filled_ts is None:
                g.filled_ts = bar_close_ts
            if closed_through and g.inverted_ts is None:
                g.inverted_ts = bar_close_ts
                break  # role has flipped; further lifecycle belongs to the iFVG story
        out.append(g)
    return out

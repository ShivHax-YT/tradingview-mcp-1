"""Session/day/opening-range price levels.

Two access patterns:

1. Single-timeframe functions (1m expected) — used where precision is
   non-negotiable, e.g. the opening range.

2. Multi-timeframe functions — take a dict of DataFrames per timeframe and use
   the FINEST timeframe that actually COVERS the requested window. Live
   collection only holds ~500 bars per timeframe (~8h of 1m), so prior-day and
   older-session levels usually resolve from 5m/15m/1h backfill instead. The
   high/low of a window is identical on any timeframe that covers it; only
   sub-bar timing resolution is lost, which levels don't need.

Coverage rule: the sliced window must have a bar within `tolerance_bars` of
BOTH edges. Partial coverage returns nothing on that timeframe — a "prior day
low" computed from half a day would be a plausible-looking lie.

All functions return None when no data covers the window. None is honest;
never fabricate a level.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from ..config import SessionsConfig
from .sessions import opening_range_bounds, session_bounds, trading_day_bounds

# Finest-first preference for level resolution.
LEVEL_TF_PREFERENCE = ("1m", "5m", "15m", "1h")

TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


def slice_window(df: pd.DataFrame, start_ts: int, end_ts: int) -> pd.DataFrame:
    """Bars with open time in [start_ts, end_ts)."""
    return df[(df["ts"] >= start_ts) & (df["ts"] < end_ts)]


def high_low(df: pd.DataFrame) -> tuple[float, float] | None:
    if df.empty:
        return None
    return float(df["high"].max()), float(df["low"].min())


def window_covered(w: pd.DataFrame, start_ts: int, end_ts: int, tf_seconds: int, tolerance_bars: int = 2) -> bool:
    """Does this sliced window actually span [start, end)? Both edges within tolerance."""
    if w.empty:
        return False
    tol = tolerance_bars * tf_seconds
    first = int(w["ts"].iloc[0])
    last = int(w["ts"].iloc[-1])
    return first <= start_ts + tol and last >= end_ts - tf_seconds - tol


def multi_tf_high_low(
    dfs: dict[str, pd.DataFrame],
    start_ts: int,
    end_ts: int,
    preference: tuple[str, ...] = LEVEL_TF_PREFERENCE,
    tolerance_bars: int = 2,
    horizon_ts: int | None = None,
) -> tuple[tuple[float, float], str] | None:
    """(high, low) of the window from the finest COVERING timeframe, plus which tf was used.

    horizon_ts = the knowledge horizon (last known bar close / as_of). Windows still
    in progress are clipped to it, so a developing session yields its so-far levels
    while a window that hasn't started yet yields None. Coverage is judged against
    the clipped window."""
    if horizon_ts is not None:
        end_ts = min(end_ts, horizon_ts)
    if end_ts <= start_ts:
        return None
    for tf in preference:
        df = dfs.get(tf)
        if df is None or df.empty:
            continue
        w = slice_window(df, start_ts, end_ts)
        if window_covered(w, start_ts, end_ts, TF_SECONDS[tf], tolerance_bars):
            hl = high_low(w)
            if hl is not None:
                return hl, tf
    return None


def multi_tf_session_high_low(
    dfs: dict[str, pd.DataFrame], day: date, session_name: str, cfg: SessionsConfig,
    horizon_ts: int | None = None,
) -> tuple[tuple[float, float], str] | None:
    s, e = session_bounds(day, session_name, cfg)
    return multi_tf_high_low(dfs, s, e, horizon_ts=horizon_ts)


def multi_tf_day_high_low(
    dfs: dict[str, pd.DataFrame], day: date, cfg: SessionsConfig,
    horizon_ts: int | None = None,
) -> tuple[tuple[float, float], str] | None:
    s, e = trading_day_bounds(day, cfg)
    return multi_tf_high_low(dfs, s, e, horizon_ts=horizon_ts)


def multi_tf_prior_day_high_low(
    dfs: dict[str, pd.DataFrame], day: date, cfg: SessionsConfig, max_lookback_days: int = 5
) -> tuple[tuple[float, float], str] | None:
    """Prior trading day H/L from the finest covering timeframe, skipping closed days."""
    d = day
    for _ in range(max_lookback_days):
        d = d - timedelta(days=1)
        res = multi_tf_day_high_low(dfs, d, cfg)
        if res is not None:
            return res
    return None


# ── Single-timeframe API (1m) ────────────────────────────────────────────────

def session_high_low(df_1m: pd.DataFrame, day: date, session_name: str, cfg: SessionsConfig) -> tuple[float, float] | None:
    s, e = session_bounds(day, session_name, cfg)
    return high_low(slice_window(df_1m, s, e))


def day_high_low(df_1m: pd.DataFrame, day: date, cfg: SessionsConfig) -> tuple[float, float] | None:
    s, e = trading_day_bounds(day, cfg)
    return high_low(slice_window(df_1m, s, e))


def prior_day_high_low(
    df_1m: pd.DataFrame, day: date, cfg: SessionsConfig, max_lookback_days: int = 5
) -> tuple[float, float] | None:
    """1m-only variant (kept for precision-sensitive callers and tests)."""
    d = day
    for _ in range(max_lookback_days):
        d = d - timedelta(days=1)
        hl = day_high_low(df_1m, d, cfg)
        if hl is not None:
            return hl
    return None


def opening_range(
    df_1m: pd.DataFrame, day: date, cfg: SessionsConfig, as_of_ts: int | None = None
) -> tuple[float, float] | None:
    """NY opening range high/low — 1m ONLY, by design (OR precision matters).
    Only defined once the window has fully elapsed (pass as_of_ts to enforce)."""
    s, e = opening_range_bounds(day, cfg)
    if as_of_ts is not None and as_of_ts < e:
        return None
    return high_low(slice_window(df_1m, s, e))

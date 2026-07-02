"""Multi-timeframe level fallback — the live 500-bar-per-timeframe reality.

Scenario mirrors production: 1m only covers the recent morning; 15m/1h backfill
covers the prior day. Levels must resolve from the finest COVERING timeframe,
and the fallback must never see future bars in as_of/replay mode.
"""

from datetime import date

from futures_copilot.features.levels import (
    multi_tf_day_high_low,
    multi_tf_high_low,
    multi_tf_prior_day_high_low,
    multi_tf_session_high_low,
    window_covered,
)

from .feature_helpers import et_ts, make_df

DAY = date(2026, 6, 24)


def _bars_15m(day_tuples):
    """(et day-tuple start, hours, high, low) -> contiguous 15m bars."""
    rows = []
    for (y, mo, d, h, mi), hours, hi, lo in day_tuples:
        start = et_ts(y, mo, d, h, mi)
        n = int(hours * 4)
        for i in range(n):
            mid = (hi + lo) / 2
            rows.append((start + i * 900, mid, hi, lo, mid))
    return rows


def _dfs_live_like():
    """1m: NY morning of 06-24 only. 15m: full prior trading day + current day so far."""
    df1 = make_df([
        (et_ts(2026, 6, 24, 9, 30) + i * 60, 105, 110, 100, 106) for i in range(60)
    ])
    df15 = make_df(_bars_15m([
        # prior trading day 06-23: 06-22 18:00 -> 06-23 17:00 (23h)
        ((2026, 6, 22, 18, 0), 23.0, 120.0, 90.0),
        # current day so far: 06-23 18:00 -> 06-24 10:30 (16.5h) with distinct asia extremes
        ((2026, 6, 23, 18, 0), 16.5, 112.0, 95.0),
    ]))
    return {"1m": df1, "15m": df15}


def test_prior_day_resolves_from_15m_when_1m_lacks_it():
    dfs = _dfs_live_like()
    res = multi_tf_prior_day_high_low(dfs, DAY, __import__("futures_copilot.config", fromlist=["SessionsConfig"]).SessionsConfig())
    assert res is not None
    (hi, lo), tf_used = res
    assert (hi, lo) == (120.0, 90.0)
    assert tf_used == "15m"


def test_asia_resolves_from_15m(config):
    dfs = _dfs_live_like()
    horizon = et_ts(2026, 6, 24, 10, 30)
    res = multi_tf_session_high_low(dfs, DAY, "asia", config.sessions, horizon_ts=horizon)
    assert res is not None
    (hi, lo), tf_used = res
    assert tf_used == "15m"
    assert (hi, lo) == (112.0, 95.0)


def test_finest_covering_timeframe_wins(config):
    # NY session so far is covered by BOTH 1m and 15m -> must use 1m.
    dfs = _dfs_live_like()
    horizon = et_ts(2026, 6, 24, 10, 30)
    res = multi_tf_session_high_low(dfs, DAY, "ny", config.sessions, horizon_ts=horizon)
    assert res is not None
    assert res[1] == "1m"
    assert res[0] == (110.0, 100.0)


def test_partial_coverage_is_rejected_not_fabricated(config):
    # Only 1m data (morning). Asia window is NOT covered by it -> None, not a fake level.
    dfs = {"1m": _dfs_live_like()["1m"]}
    horizon = et_ts(2026, 6, 24, 10, 30)
    assert multi_tf_session_high_low(dfs, DAY, "asia", config.sessions, horizon_ts=horizon) is None


def test_developing_window_clipped_at_horizon(config):
    # Day range mid-morning: window end is clipped to horizon; coverage judged to horizon.
    dfs = _dfs_live_like()
    horizon = et_ts(2026, 6, 24, 10, 30)
    res = multi_tf_day_high_low(dfs, DAY, config.sessions, horizon_ts=horizon)
    assert res is not None
    (hi, lo), tf_used = res
    assert tf_used == "15m"           # 1m starts at 09:30, can't cover from 18:00
    assert (hi, lo) == (112.0, 95.0)


def test_window_not_started_returns_none():
    dfs = _dfs_live_like()
    start = et_ts(2026, 6, 24, 12, 0)
    end = et_ts(2026, 6, 24, 14, 0)
    assert multi_tf_high_low(dfs, start, end, horizon_ts=start) is None


def test_window_covered_edges():
    df = make_df([(1_750_000_000 + i * 900, 100, 101, 99, 100) for i in range(8)])  # 2h of 15m
    w_start, w_end = 1_750_000_000, 1_750_000_000 + 8 * 900
    assert window_covered(df, w_start, w_end, 900)
    assert not window_covered(df, w_start - 4 * 900, w_end, 900)   # missing front edge
    assert not window_covered(df.iloc[:2], w_start, w_end, 900)    # missing back edge

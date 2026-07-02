from datetime import date

from futures_copilot.features.levels import (
    day_high_low,
    opening_range,
    prior_day_high_low,
    session_high_low,
)

from .feature_helpers import et_ts, make_df

DAY = date(2026, 6, 24)


def _day_df():
    rows = [
        # prior trading day (2026-06-23): bars from 06-22 19:00 and 06-23 10:00
        (et_ts(2026, 6, 22, 19, 0), 100, 120, 90, 110),
        (et_ts(2026, 6, 23, 10, 0), 110, 118, 92, 111),
        # asia session of trading day 06-24 (starts 06-23 18:00 ET)
        (et_ts(2026, 6, 23, 20, 0), 100, 105, 95, 101),
        (et_ts(2026, 6, 23, 22, 0), 101, 104, 96, 102),
        # london
        (et_ts(2026, 6, 24, 4, 0), 102, 108, 97, 103),
        # NY opening range window (09:30-09:45)
        (et_ts(2026, 6, 24, 9, 30), 103, 110, 100, 105),
        (et_ts(2026, 6, 24, 9, 40), 105, 109, 101, 106),
        # NY after the opening range
        (et_ts(2026, 6, 24, 11, 0), 106, 112, 99, 107),
    ]
    return make_df(rows)


def test_session_high_low(config):
    df = _day_df()
    assert session_high_low(df, DAY, "asia", config.sessions) == (105.0, 95.0)
    assert session_high_low(df, DAY, "london", config.sessions) == (108.0, 97.0)
    assert session_high_low(df, DAY, "ny", config.sessions) == (112.0, 99.0)


def test_session_high_low_none_when_no_bars(config):
    df = _day_df()
    assert session_high_low(df, date(2026, 6, 26), "asia", config.sessions) is None


def test_prior_day_high_low(config):
    df = _day_df()
    assert prior_day_high_low(df, DAY, config.sessions) == (120.0, 90.0)


def test_prior_day_skips_weekend(config):
    # Only bars on Friday 2026-06-19; prior day for Monday 06-22 must walk back to it.
    df = make_df([(et_ts(2026, 6, 19, 10, 0), 100, 115, 85, 100)])
    assert prior_day_high_low(df, date(2026, 6, 22), config.sessions) == (115.0, 85.0)


def test_opening_range_only_after_window_elapsed(config):
    df = _day_df()
    before = et_ts(2026, 6, 24, 9, 44)
    after = et_ts(2026, 6, 24, 9, 45)
    assert opening_range(df, DAY, config.sessions, as_of_ts=before) is None
    assert opening_range(df, DAY, config.sessions, as_of_ts=after) == (110.0, 100.0)


def test_opening_range_excludes_later_bars(config):
    # The 11:00 bar (high 112) must not leak into the 09:30-09:45 range.
    df = _day_df()
    hl = opening_range(df, DAY, config.sessions, as_of_ts=et_ts(2026, 6, 24, 12, 0))
    assert hl == (110.0, 100.0)


def test_day_high_low(config):
    df = _day_df()
    assert day_high_low(df, DAY, config.sessions) == (112.0, 95.0)

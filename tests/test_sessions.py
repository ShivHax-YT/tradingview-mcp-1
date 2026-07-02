from datetime import date

from futures_copilot.features.sessions import (
    opening_range_bounds,
    session_at,
    session_bounds,
    trading_day,
    trading_day_bounds,
)

from .feature_helpers import et_ts


def test_trading_day_regular_hours(config):
    cfg = config.sessions
    # Wed 2026-06-24 10:00 ET -> trading day 2026-06-24
    assert trading_day(et_ts(2026, 6, 24, 10, 0), cfg) == date(2026, 6, 24)
    # 17:59 ET still belongs to the closing day
    assert trading_day(et_ts(2026, 6, 24, 16, 30), cfg) == date(2026, 6, 24)


def test_trading_day_rolls_at_1800_et(config):
    cfg = config.sessions
    assert trading_day(et_ts(2026, 6, 24, 18, 0), cfg) == date(2026, 6, 25)
    assert trading_day(et_ts(2026, 6, 24, 23, 59), cfg) == date(2026, 6, 25)
    assert trading_day(et_ts(2026, 6, 25, 2, 0), cfg) == date(2026, 6, 25)


def test_trading_day_bounds_cover_18_to_17(config):
    cfg = config.sessions
    s, e = trading_day_bounds(date(2026, 6, 24), cfg)
    assert s == et_ts(2026, 6, 23, 18, 0)
    assert e == et_ts(2026, 6, 24, 17, 0)


def test_session_bounds_asia_wraps_calendar_midnight(config):
    cfg = config.sessions
    s, e = session_bounds(date(2026, 6, 24), "asia", cfg)
    assert s == et_ts(2026, 6, 23, 18, 0)
    assert e == et_ts(2026, 6, 24, 3, 0)


def test_session_at_labels(config):
    cfg = config.sessions
    assert session_at(et_ts(2026, 6, 24, 22, 0), cfg) == "asia"     # evening -> next day's asia
    assert session_at(et_ts(2026, 6, 24, 4, 0), cfg) == "london"
    assert session_at(et_ts(2026, 6, 24, 10, 0), cfg) == "ny"
    assert session_at(et_ts(2026, 6, 24, 16, 30), cfg) is None      # post-close dead zone
    assert session_at(et_ts(2026, 6, 24, 17, 30), cfg) is None      # maintenance break


def test_opening_range_bounds(config):
    cfg = config.sessions
    s, e = opening_range_bounds(date(2026, 6, 24), cfg)
    assert s == et_ts(2026, 6, 24, 9, 30)
    assert e == et_ts(2026, 6, 24, 9, 45)


def test_dst_safety_january(config):
    # Same wall-clock rules in EST (winter): 18:00 ET boundary still rolls the day.
    cfg = config.sessions
    assert trading_day(et_ts(2026, 1, 14, 18, 0), cfg) == date(2026, 1, 15)
    assert session_at(et_ts(2026, 1, 14, 10, 0), cfg) == "ny"

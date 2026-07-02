"""Trading-day and session time logic.

CME index futures trade 18:00 ET -> 17:00 ET next day, with a 17:00-18:00
maintenance break. The "trading day" is labeled by its END date: bars from
Tue 18:00 ET onward belong to Wednesday's trading day.

All bar timestamps are UTC epoch seconds; all session definitions are
wall-clock America/New_York (DST handled by zoneinfo).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..config import SessionsConfig

ET = ZoneInfo("America/New_York")

SESSION_NAMES = ("asia", "london", "ny")


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def to_et(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=ET)


def et_epoch(d: date, t: time) -> int:
    return int(datetime.combine(d, t, tzinfo=ET).timestamp())


def trading_day(ts: int, cfg: SessionsConfig) -> date:
    """Trading day a bar belongs to. Bars at/after 18:00 ET belong to the NEXT calendar day."""
    dt = to_et(ts)
    if dt.time() >= _hm(cfg.trading_day_start):
        return dt.date() + timedelta(days=1)
    return dt.date()


def trading_day_bounds(day: date, cfg: SessionsConfig) -> tuple[int, int]:
    """[start, end) epochs for a trading day: prior 18:00 ET -> 17:00 ET (maintenance start)."""
    start = et_epoch(day - timedelta(days=1), _hm(cfg.trading_day_start))
    end = et_epoch(day, _hm(cfg.maintenance_break[0]))
    return start, end


def session_bounds(day: date, name: str, cfg: SessionsConfig) -> tuple[int, int]:
    """[start, end) epochs of a named session within a trading day."""
    if name not in SESSION_NAMES:
        raise ValueError(f"unknown session {name!r}; expected one of {SESSION_NAMES}")
    start_s, end_s = getattr(cfg, name)
    tds = _hm(cfg.trading_day_start)
    st, en = _hm(start_s), _hm(end_s)
    # Times at/after the trading-day start (e.g. Asia 18:00) fall on the PRIOR calendar date.
    start_date = day - timedelta(days=1) if st >= tds else day
    end_date = day - timedelta(days=1) if en > tds else day
    return et_epoch(start_date, st), et_epoch(end_date, en)


def session_at(ts: int, cfg: SessionsConfig) -> str | None:
    """Which session a timestamp falls in, or None (16:00-18:00 ET dead zone)."""
    day = trading_day(ts, cfg)
    for name in SESSION_NAMES:
        s, e = session_bounds(day, name, cfg)
        if s <= ts < e:
            return name
    return None


def opening_range_bounds(day: date, cfg: SessionsConfig) -> tuple[int, int]:
    """[start, end) of the NY opening range window."""
    s, _ = session_bounds(day, "ny", cfg)
    return s, s + cfg.opening_range_minutes * 60

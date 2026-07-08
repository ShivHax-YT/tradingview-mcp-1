"""Static CME equity index contract roll-date helpers."""

from __future__ import annotations

from datetime import date, timedelta

ROLL_WINDOW_DAYS = 3

EQUITY_INDEX_ROLL_DATES: dict[int, tuple[date, ...]] = {
    2026: (
        date(2026, 3, 16),
        date(2026, 6, 15),
        date(2026, 9, 14),
        date(2026, 12, 14),
    ),
    2027: (
        date(2027, 3, 15),
        date(2027, 6, 14),
        date(2027, 9, 13),
        date(2027, 12, 13),
    ),
    2028: (
        date(2028, 3, 13),
        date(2028, 6, 12),
        date(2028, 9, 11),
        date(2028, 12, 11),
    ),
}

_ROLL_DATES = tuple(d for dates in EQUITY_INDEX_ROLL_DATES.values() for d in dates)
_LAST_ROLL_WINDOW_DATE = max(_ROLL_DATES) + timedelta(days=ROLL_WINDOW_DAYS)


def get_next_roll_date(dt: date) -> date | None:
    """Return the next mapped roll date on or after ``dt``.

    The static calendar intentionally ends in 2028. Future dates return None
    instead of guessing.
    """
    if dt.year > 2028:
        return None
    return next((roll for roll in _ROLL_DATES if roll >= dt), None)


def is_in_roll_window(dt: date) -> bool:
    """Whether ``dt`` is inside a mapped +/- 3 day roll window."""
    if dt > _LAST_ROLL_WINDOW_DATE:
        return False
    return any(abs((dt - roll).days) <= ROLL_WINDOW_DAYS for roll in _ROLL_DATES)

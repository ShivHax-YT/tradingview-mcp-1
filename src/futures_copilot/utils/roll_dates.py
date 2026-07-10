"""CME equity index contract roll-date helpers.

Hand-verified static calendar for 2026-2028 plus a COMPUTED fallback
(Monday preceding the 3rd Friday of Mar/Jun/Sep/Dec) for later years.
The computed rule reproduces every hand-verified static entry — pinned by
test_computed_rolls_match_static_table — so a trader in 2029+ is never
silently unprotected. `roll_calendar_source()` tells callers (dashboard)
whether the active year is verified or computed, so they can show a
"verify against CME" reminder instead of a silent green check.
"""

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


def _third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)                     # 3rd Friday is the 15th-21st
    return d + timedelta(days=(4 - d.weekday()) % 7)


def _computed_rolls(year: int) -> tuple[date, ...]:
    """Monday before the 3rd Friday of each contract month (CME convention)."""
    return tuple(_third_friday(year, m) - timedelta(days=4) for m in (3, 6, 9, 12))


def rolls_for_year(year: int) -> tuple[date, ...]:
    """Static (hand-verified) dates when mapped; computed dates beyond the map."""
    return EQUITY_INDEX_ROLL_DATES.get(year, _computed_rolls(year))


def roll_calendar_source(dt: date) -> str:
    """'static' when dt's year is hand-verified, else 'computed'."""
    return "static" if dt.year in EQUITY_INDEX_ROLL_DATES else "computed"


def get_next_roll_date(dt: date) -> date:
    """Next roll date on or after ``dt``. Never None — any year."""
    for year in (dt.year, dt.year + 1):
        for roll in rolls_for_year(year):
            if roll >= dt:
                return roll
    return rolls_for_year(dt.year + 1)[0]         # defensive; unreachable


def is_in_roll_window(dt: date) -> bool:
    """Whether ``dt`` is inside a +/- ROLL_WINDOW_DAYS roll window, any year."""
    for year in (dt.year - 1, dt.year, dt.year + 1):
        for roll in rolls_for_year(year):
            if abs((dt - roll).days) <= ROLL_WINDOW_DAYS:
                return True
    return False

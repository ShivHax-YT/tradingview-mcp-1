"""Deterministic feature engine.

Every function here is pure: candles in, values out. No network, no state,
no lookahead — a feature computed "as of" bar N uses only bars <= N, and
anything that needs future confirmation (swings, FVGs) carries an explicit
confirmation timestamp so callers can filter honestly.
"""

from .sessions import trading_day, trading_day_bounds, session_bounds, session_at, opening_range_bounds
from .levels import session_high_low, day_high_low, prior_day_high_low, opening_range
from .swings import SwingPoint, find_swings, swings_confirmed_by
from .sweeps import Sweep, Reclaim, detect_sweep, detect_reclaim
from .structure import StructureEvent, detect_mss, detect_cisd
from .fvg import FVG, detect_fvgs
from .vwap import anchored_vwap, day_vwap
from .volatility import atr
from .pricing import rr, position_in_range, premium_discount, points_to_ticks, points_to_dollars

__all__ = [
    "trading_day", "trading_day_bounds", "session_bounds", "session_at", "opening_range_bounds",
    "session_high_low", "day_high_low", "prior_day_high_low", "opening_range",
    "SwingPoint", "find_swings", "swings_confirmed_by",
    "Sweep", "Reclaim", "detect_sweep", "detect_reclaim",
    "StructureEvent", "detect_mss", "detect_cisd",
    "FVG", "detect_fvgs",
    "anchored_vwap", "day_vwap",
    "atr",
    "rr", "position_in_range", "premium_discount", "points_to_ticks", "points_to_dollars",
]

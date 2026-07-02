"""Premium/discount, R:R, and tick/dollar conversion helpers."""

from __future__ import annotations

from ..config import SymbolSpec


def rr(entry: float, stop: float, target: float) -> float:
    """Reward:risk multiple. Validates geometry: stop and target must sit on
    opposite sides of entry (otherwise the trade is nonsense and we refuse to
    return a plausible-looking number)."""
    risk = entry - stop
    reward = target - entry
    if risk == 0:
        raise ValueError("stop equals entry; risk is zero")
    if reward == 0:
        raise ValueError("target equals entry; reward is zero")
    if (risk > 0) == (reward < 0):
        raise ValueError(
            f"stop ({stop}) and target ({target}) are on the same side of entry ({entry})"
        )
    return abs(reward) / abs(risk)


def position_in_range(price: float, range_low: float, range_high: float) -> float:
    """0.0 = at the low, 1.0 = at the high. Values outside [0,1] mean price left the range."""
    if range_high <= range_low:
        raise ValueError(f"invalid range: high {range_high} <= low {range_low}")
    return (price - range_low) / (range_high - range_low)


def premium_discount(price: float, range_low: float, range_high: float, band: float = 0.05) -> str:
    """'discount' below equilibrium, 'premium' above, 'equilibrium' within +/-band of 0.5."""
    pos = position_in_range(price, range_low, range_high)
    if pos < 0.5 - band:
        return "discount"
    if pos > 0.5 + band:
        return "premium"
    return "equilibrium"


def points_to_ticks(points: float, spec: SymbolSpec) -> float:
    return points / spec.tick_size


def points_to_dollars(points: float, spec: SymbolSpec, contracts: int = 1) -> float:
    """USD value of a move of `points`, per `contracts` (micro contracts: MNQ $0.50/tick)."""
    return points_to_ticks(points, spec) * spec.tick_value * contracts

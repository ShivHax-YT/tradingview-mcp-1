"""Strategy plugins. detect(context) -> [SignalCandidate]; never decisions."""

from .base import SignalCandidate, Strategy, StrategyContext
from .engine import REGISTRY, ScanResult, build_context, enabled_strategies, scan
from .liquidity_trap import SessionLiquidityTrap
from .orb import OrbBreakout

__all__ = [
    "SignalCandidate", "Strategy", "StrategyContext",
    "REGISTRY", "ScanResult", "build_context", "enabled_strategies", "scan",
    "SessionLiquidityTrap", "OrbBreakout",
]

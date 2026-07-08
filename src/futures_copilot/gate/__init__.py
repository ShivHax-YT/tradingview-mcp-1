"""Risk/freshness gate — the ONLY component allowed to emit trade decisions."""

from .risk_gate import (
    CheckResult,
    GateDecision,
    GateOutput,
    evaluate,
    golden_hour_status,
    trade_governor_status,
)

__all__ = [
    "CheckResult",
    "GateDecision",
    "GateOutput",
    "evaluate",
    "golden_hour_status",
    "trade_governor_status",
]

"""Risk/freshness gate — the ONLY component allowed to emit trade decisions."""

from .risk_gate import CheckResult, GateDecision, GateOutput, evaluate

__all__ = ["CheckResult", "GateDecision", "GateOutput", "evaluate"]

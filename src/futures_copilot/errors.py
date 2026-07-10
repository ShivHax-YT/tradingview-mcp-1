"""Typed errors. The data layer fails loudly and precisely — never silently.

There is deliberately no fallback data path: if the TradingView MCP bridge
cannot provide data, the correct behavior is a clear, actionable error.
"""

from __future__ import annotations


class CopilotError(Exception):
    """Base error. `code` is stable and machine-readable; `hint` tells the human what to do."""

    code = "COPILOT_ERROR"

    def __init__(self, message: str, hint: str = ""):
        self.hint = hint
        super().__init__(message)

    def __str__(self) -> str:  # pragma: no cover - formatting
        base = f"[{self.code}] {super().__str__()}"
        return f"{base}\n  fix: {self.hint}" if self.hint else base


class ConfigError(CopilotError):
    code = "CONFIG_ERROR"


class DataSourceError(CopilotError):
    code = "DATA_SOURCE_ERROR"


class BridgeNotFound(DataSourceError):
    """MCP bridge server.js or node executable missing."""

    code = "BRIDGE_NOT_FOUND"


class CdpUnreachable(DataSourceError):
    """Bridge started but TradingView Desktop is not reachable on the CDP port."""

    code = "CDP_UNREACHABLE"


class ChartNotReady(DataSourceError):
    """Chart exists but returned no usable bars (still loading, empty symbol...)."""

    code = "CHART_NOT_READY"


class SymbolMismatch(DataSourceError):
    """Chart is on a different symbol/timeframe than requested. Never store mismatched bars."""

    code = "SYMBOL_MISMATCH"


class NoBarsReturned(DataSourceError):
    code = "NO_BARS_RETURNED"


class UnsupportedTimeframe(DataSourceError):
    code = "UNSUPPORTED_TIMEFRAME"


class FixtureError(DataSourceError):
    code = "FIXTURE_ERROR"


class PacketError(CopilotError):
    """A prompt packet cannot be built without trustworthy required data."""

    code = "PACKET_ERROR"


class ReviewError(CopilotError):
    """A deterministic journal review could not be generated or written."""

    code = "REVIEW_ERROR"

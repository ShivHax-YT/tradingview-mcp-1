"""Offline unit tests for the tvmcp adapter's pure logic.

The live path (node bridge + TradingView Desktop + CDP) can't run in CI or a
sandbox; it is exercised on the user's machine via `copilot health` and
`copilot backfill`. Everything testable without a chart is tested here.
"""

import asyncio
import threading

import pytest

from futures_copilot.data.tvmcp import (
    ALLOWED_TOOLS,
    TradingViewMcpCandleSource,
    _normalize_epoch_seconds,
    _symbol_root,
)
from futures_copilot.errors import (
    BridgeNotFound,
    DataSourceError,
    SymbolMismatch,
    UnsupportedTimeframe,
)


def test_epoch_normalization():
    assert _normalize_epoch_seconds(1_750_000_000) == 1_750_000_000       # already seconds
    assert _normalize_epoch_seconds(1_750_000_000_000) == 1_750_000_000   # milliseconds
    assert _normalize_epoch_seconds(1_750_000_000.0) == 1_750_000_000     # float


def test_symbol_root():
    assert _symbol_root("CME_MINI:MNQ1!") == "MNQ1!"
    assert _symbol_root("MNQ1!") == "MNQ1!"
    assert _symbol_root("cme_mini:mes1! ") == "MES1!"


def test_allowlist_is_read_only_tools():
    # No drawing, alerts, UI automation, replay-trading, or order-like tools — ever.
    forbidden_fragments = ("alert", "draw", "trade", "order", "replay", "ui_", "pine_set")
    for tool in ALLOWED_TOOLS:
        assert not any(f in tool for f in forbidden_fragments), tool


def test_unknown_tool_rejected(config):
    src = TradingViewMcpCandleSource(config)
    with pytest.raises(DataSourceError):
        src._call("alert_create", {})


def test_missing_bridge_raises_bridge_not_found(config):
    config.data.tvmcp.server_path = "vendor/does-not-exist/server.js"
    src = TradingViewMcpCandleSource(config)
    with pytest.raises(BridgeNotFound):
        src.health_check()


def test_unmapped_timeframe_raises(config):
    src = TradingViewMcpCandleSource(config)
    with pytest.raises(UnsupportedTimeframe):
        src._resolution_for("2m")


def test_unknown_symbol_raises(config):
    src = TradingViewMcpCandleSource(config)
    with pytest.raises(SymbolMismatch):
        src._chart_symbol_for("ES")  # only MNQ/MES configured


def test_resolution_mapping(config):
    src = TradingViewMcpCandleSource(config)
    assert src._resolution_for("1m") == "1"
    assert src._resolution_for("1h") == "60"


def test_close_waits_for_lifecycle_cleanup_after_cancel(config):
    """A cancelled concurrent Future must not pre-empt in-task finalizers."""
    src = TradingViewMcpCandleSource(config)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    src._loop = loop
    src._thread = thread
    src._lifecycle_done = threading.Event()
    src._cancel_requested = threading.Event()
    entered = threading.Event()
    cleaned = threading.Event()

    async def lifecycle():
        src._lifecycle_task = asyncio.current_task()
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            # Yield once: this is the cleanup turn the old close() skipped by
            # stopping the loop as soon as the concurrent Future was cancelled.
            await asyncio.sleep(0.05)
            cleaned.set()
            src._lifecycle_task = None
            src._lifecycle_done.set()

    class TimeoutThenFuture:
        """Force close() through its timeout/cancellation fallback once."""

        def __init__(self, future):
            self.future = future
            self.first = True

        def result(self, timeout=None):
            if self.first:
                self.first = False
                raise TimeoutError("forced lifecycle timeout")
            return self.future.result(timeout)

    src._lifecycle_fut = TimeoutThenFuture(
        asyncio.run_coroutine_threadsafe(lifecycle(), loop)
    )
    assert entered.wait(1)
    src.close()

    assert cleaned.is_set()
    assert not thread.is_alive()
    assert src._loop is None

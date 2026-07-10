"""Offline unit tests for the tvmcp adapter's pure logic.

The live path (node bridge + TradingView Desktop + CDP) can't run in CI or a
sandbox; it is exercised on the user's machine via `copilot health` and
`copilot backfill`. Everything testable without a chart is tested here.
"""

import asyncio
import threading
import time
from contextlib import nullcontext

import pytest

from futures_copilot.data.tvmcp import (
    ALLOWED_TOOLS,
    TradingViewMcpCandleSource,
    _normalize_epoch_seconds,
    _symbol_root,
)
from futures_copilot.data.collector import collect_once
from futures_copilot.errors import (
    BridgeNotFound,
    DataLatencyError,
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


def _stub_ohlcv(monkeypatch, source, bars):
    monkeypatch.setattr(source, "_chart_transaction", lambda: nullcontext())
    monkeypatch.setattr(source, "_ensure_chart_locked", lambda *_args: None)
    monkeypatch.setattr(source, "_call", lambda *_args, **_kwargs: {"bars": bars})


def _raw_bar(**updates):
    bar = {
        "time": 1_750_000_000,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 10.0,
    }
    bar.update(updates)
    return bar


@pytest.mark.parametrize(
    "bad_bar",
    [
        _raw_bar(open=float("nan")),
        _raw_bar(high=float("inf")),
        _raw_bar(low=float("-inf")),
        _raw_bar(close=0),
        _raw_bar(open=-1),
        _raw_bar(high=98, low=99),
        _raw_bar(high=100, open=101),
        _raw_bar(low=100, close=99.5),
        _raw_bar(volume=-1),
        _raw_bar(volume=float("nan")),
    ],
)
def test_get_candles_rejects_malformed_bar(config, monkeypatch, bad_bar):
    source = TradingViewMcpCandleSource(config)
    _stub_ohlcv(monkeypatch, source, [_raw_bar(time=1_749_999_940), bad_bar])

    with pytest.raises(DataSourceError) as exc:
        source.get_candles("MNQ", "1m", 2)

    assert "offending bar=" in str(exc.value)


def test_malformed_middle_bar_rejects_entire_collection_batch(config, store, monkeypatch):
    source = TradingViewMcpCandleSource(config)
    bars = [
        _raw_bar(time=1_749_999_880),
        _raw_bar(time=1_749_999_940, low=102),
        _raw_bar(time=1_750_000_000),
    ]
    _stub_ohlcv(monkeypatch, source, bars)

    report = collect_once(config, source, store, ["MNQ"])

    assert store.last_ts("MNQ", "1m") is None
    assert report.errors and "offending bar=" in report.errors[0]


def test_live_freshness_allows_five_seconds_and_rejects_overdue_bar(config, monkeypatch):
    source = TradingViewMcpCandleSource(config)
    source._last_observed_bar_ts[("MNQ", "1m")] = 1_000

    monkeypatch.setattr("futures_copilot.data.tvmcp.time.time", lambda: 1_065.0)
    source.assert_live_freshness("MNQ", "1m")

    monkeypatch.setattr("futures_copilot.data.tvmcp.time.time", lambda: 1_065.001)
    with pytest.raises(DataLatencyError, match="stale live data"):
        source.assert_live_freshness("MNQ", "1m")


def test_stale_live_collection_fails_closed_before_store(config, store, monkeypatch):
    source = TradingViewMcpCandleSource(config)
    active_ts = 1_750_000_000
    _stub_ohlcv(monkeypatch, source, [
        _raw_bar(time=active_ts - 60),
        _raw_bar(time=active_ts),
    ])
    monkeypatch.setattr("futures_copilot.data.tvmcp.time.time", lambda: active_ts + 66.0)

    report = collect_once(config, source, store, ["MNQ"])

    assert store.last_ts("MNQ", "1m") is None
    assert report.errors and "DATA_LATENCY" in report.errors[0]


def test_live_quote_without_source_timestamp_fails_closed(config, monkeypatch):
    source = TradingViewMcpCandleSource(config)
    monkeypatch.setattr(source, "_chart_transaction", lambda: nullcontext())
    monkeypatch.setattr(source, "_ensure_chart_locked", lambda *_args: None)
    monkeypatch.setattr(source, "_call", lambda *_args, **_kwargs: {"header_price": 100})

    with pytest.raises(DataLatencyError, match="no source timestamp"):
        source.get_quote("MNQ")


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


def test_workspace_chart_lock_serializes_sources(config, tmp_path):
    config.root = tmp_path
    first = TradingViewMcpCandleSource(config)
    second = TradingViewMcpCandleSource(config)
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()

    def hold_first():
        with first._chart_transaction():
            entered.set()
            assert release.wait(2)

    def enter_second():
        assert entered.wait(2)
        with second._chart_transaction():
            second_entered.set()

    t1 = threading.Thread(target=hold_first)
    t2 = threading.Thread(target=enter_second)
    t1.start()
    t2.start()
    assert entered.wait(2)
    time.sleep(0.1)
    assert not second_entered.is_set()
    release.set()
    t1.join(2)
    t2.join(2)

    assert second_entered.is_set()
    assert (tmp_path / ".copilot.lock").exists()

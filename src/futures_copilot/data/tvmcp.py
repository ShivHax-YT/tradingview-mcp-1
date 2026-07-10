"""TradingViewMcpCandleSource — the PRODUCT data path.

Reads OHLCV bars from the user's locally running TradingView Desktop
(with the user's own CME market-data subscription) through the
tradesdontlie/tradingview-mcp bridge (Node, MCP over stdio, CDP :9222).

Hard rules encoded here:
- Verify the chart is on the requested symbol+timeframe BEFORE reading bars;
  set it if needed, re-verify, and refuse to store mismatched data (SymbolMismatch).
- Normalize bar times to UTC epoch seconds.
- Drop the still-forming last bar by default.
- Fail loudly with typed errors. There is no fallback data source, by design.

This module never places orders. The bridge has no order tools; this adapter
only ever calls read/navigation tools, listed in ALLOWED_TOOLS.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

from ..config import Config
from ..errors import (
    BridgeNotFound,
    CdpUnreachable,
    ChartNotReady,
    DataSourceError,
    NoBarsReturned,
    SymbolMismatch,
    UnsupportedTimeframe,
)
from ..models import Candle
from ..utils.symbols import normalize_symbol
from .base import CandleSource

# Explicit allowlist: every bridge tool this adapter is permitted to call.
# Chart reading + navigation only. No alerts, no drawing, no UI automation,
# no replay trading, and (the bridge has none anyway) no order placement.
ALLOWED_TOOLS = {
    "tv_health_check",
    "chart_get_state",
    "chart_set_symbol",
    "chart_set_timeframe",
    "data_get_ohlcv",
    "quote_get",
    "symbol_info",
}


def _normalize_epoch_seconds(t: float | int) -> int:
    """Bridge bar times come from TradingView internals; normalize ms -> s if needed."""
    t = int(t)
    return t // 1000 if t > 10_000_000_000 else t


def _symbol_root(chart_symbol: str) -> str:
    """'CME_MINI:MNQ1!' -> 'MNQ1!' for tolerant comparison with chart state."""
    return chart_symbol.split(":")[-1].strip().upper()


def _canonical_symbol_root(chart_symbol: str) -> str:
    """'CME_MINI:MNQ1!' -> 'MNQ' for exact root comparison."""
    return normalize_symbol(chart_symbol)


class TradingViewMcpCandleSource(CandleSource):
    name = "tvmcp"

    def __init__(self, config: Config):
        self.config = config
        self.tv = config.data.tvmcp
        self.server_path = config.resolve(self.tv.server_path)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session = None
        self._ready: threading.Event | None = None
        self._shutdown: asyncio.Event | None = None
        self._lifecycle_fut = None
        self._lifecycle_error: BaseException | None = None
        # Set only after _lifecycle has exited its AsyncExitStack. A concurrent
        # Future reports cancellation before that in-task cleanup is complete.
        self._lifecycle_done: threading.Event | None = None
        self._lifecycle_task: asyncio.Task | None = None
        self._cancel_requested: threading.Event | None = None
        self._reaper_started = False

    # ── connection management (background loop, single-task lifecycle) ───────
    def _ensure_connected(self) -> None:
        if self._session is not None:
            return
        if self._loop is not None:
            # A previous attempt failed half-open. Tear it down BEFORE retrying,
            # or every retry leaks a loop + thread (+ a node child process).
            self.close()
            if self._loop is not None:
                raise CdpUnreachable(
                    "MCP bridge cleanup is still in progress",
                    hint="wait for the prior bridge shutdown to finish, then retry",
                )
        if not self.server_path.exists():
            raise BridgeNotFound(
                f"MCP bridge not found at {self.server_path}",
                hint="clone tradesdontlie/tradingview-mcp into vendor/ and run `npm install` there "
                     "(see README: Setup step 2)",
            )
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._ready = threading.Event()
        self._lifecycle_error = None
        self._lifecycle_done = threading.Event()
        self._lifecycle_task = None
        self._cancel_requested = threading.Event()
        self._reaper_started = False
        self._lifecycle_fut = asyncio.run_coroutine_threadsafe(self._lifecycle(), self._loop)
        ok = self._ready.wait(timeout=self.tv.request_timeout_s)
        if not ok or self._session is None:
            err = self._lifecycle_error
            self.close()   # cancels the lifecycle task -> stack unwinds IN-TASK -> node dies
            if isinstance(err, DataSourceError):
                raise err
            raise CdpUnreachable(
                f"could not start/connect MCP bridge: {err or 'timed out'}",
                hint="is Node installed? is TradingView Desktop running with "
                     "--remote-debugging-port=9222? (use vendor/tradingview-mcp/scripts/launch_tv_debug.bat)",
            ) from err

    async def _lifecycle(self) -> None:
        """Own the MCP transport for its WHOLE life inside ONE asyncio task.

        mcp's stdio_client is anyio-based: its cancel scopes must be exited by
        the same task that entered them. The previous design entered the stack
        in one task (_connect) and aclose()d it from another (close), which
        raises RuntimeError inside anyio — silently swallowed — and leaked the
        spawned node child process on every close and every failed connect.
        Here, enter and exit both happen in THIS task; close() signals and then
        waits for this task to report that its stack has fully unwound.
        """
        self._lifecycle_task = asyncio.current_task()
        try:
            from contextlib import AsyncExitStack

            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            self._shutdown = asyncio.Event()
            # close() may race the task's first turn. Observe its request here
            # before opening any transport, rather than cancelling only the
            # cross-thread Future proxy.
            if self._cancel_requested is not None and self._cancel_requested.is_set():
                raise asyncio.CancelledError
            async with AsyncExitStack() as stack:
                params = StdioServerParameters(
                    command=self.tv.node_command,
                    args=[str(self.server_path)],
                )
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self._session = session
                self._ready.set()
                await self._shutdown.wait()      # hold the transport open until close()
        except BaseException as e:               # includes CancelledError from close()
            self._lifecycle_error = e
            if self._ready is not None:
                self._ready.set()
            raise
        finally:
            self._session = None                 # stack has unwound in-task: node is dead
            self._lifecycle_task = None
            if self._lifecycle_done is not None:
                self._lifecycle_done.set()

    def _cancel_lifecycle_task(self) -> None:
        """Request cancellation of the actual lifecycle task on its own loop."""
        task = self._lifecycle_task
        if task is not None and not task.done():
            task.cancel()

    def _clear_lifecycle(self, loop: asyncio.AbstractEventLoop) -> None:
        """Forget lifecycle state only if it still belongs to ``loop``."""
        if self._loop is not loop:
            return
        self._session = None
        self._loop = None
        self._thread = None
        self._ready = None
        self._shutdown = None
        self._lifecycle_fut = None
        self._lifecycle_error = None
        self._lifecycle_done = None
        self._lifecycle_task = None
        self._cancel_requested = None
        self._reaper_started = False

    def _stop_and_close_loop(
        self, loop: asyncio.AbstractEventLoop, thread: threading.Thread | None,
    ) -> bool:
        """Stop, join, and close a fully unwound lifecycle loop."""
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:  # already closed
            pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        if thread is not None and thread.is_alive():
            return False
        try:
            loop.close()
        except RuntimeError:
            return False
        self._clear_lifecycle(loop)
        return True

    def _reap_after_lifecycle(
        self, loop: asyncio.AbstractEventLoop, thread: threading.Thread | None,
        done: threading.Event,
    ) -> None:
        """Finish cleanup later when a hung cancellation finally unwinds.

        Never stop the event loop while the anyio-owned AsyncExitStack is still
        unwinding. The daemon reaper keeps the loop alive until the lifecycle
        task itself declares cleanup complete, then performs the normal close.
        """
        if self._reaper_started:
            return
        self._reaper_started = True

        def reap() -> None:
            done.wait()
            while not self._stop_and_close_loop(loop, thread):
                if self._loop is not loop:
                    return
                threading.Event().wait(0.1)

        threading.Thread(target=reap, daemon=True).start()

    def close(self) -> None:
        loop = self._loop
        if loop is None or self._reaper_started:
            return
        thread = self._thread
        done = self._lifecycle_done
        cleanup_complete = done is None
        try:
            if self._shutdown is not None:
                loop.call_soon_threadsafe(self._shutdown.set)
            if self._lifecycle_fut is not None:
                try:
                    self._lifecycle_fut.result(timeout=10)    # same-task cleanup completes
                except BaseException:
                    # Never cancel the concurrent Future proxy: it reports
                    # cancellation before the actual task's async finalizers
                    # run. Request cancellation on the MCP loop instead, then
                    # wait for _lifecycle_done before stopping that loop.
                    if self._cancel_requested is not None:
                        self._cancel_requested.set()
                    try:
                        loop.call_soon_threadsafe(self._cancel_lifecycle_task)
                    except RuntimeError:  # loop already closed
                        pass
                if done is not None:
                    cleanup_complete = done.wait(timeout=5)
        finally:
            if cleanup_complete:
                if not self._stop_and_close_loop(loop, thread) and done is not None:
                    self._reap_after_lifecycle(loop, thread, done)
            elif done is not None:
                self._reap_after_lifecycle(loop, thread, done)

    # ── low-level tool call ───────────────────────────────────────────────────
    def _call(self, tool: str, args: dict | None = None) -> dict:
        if tool not in ALLOWED_TOOLS:
            raise DataSourceError(f"tool {tool!r} is not on the adapter allowlist {sorted(ALLOWED_TOOLS)}")
        self._ensure_connected()
        fut = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(tool, args or {}), self._loop
        )
        try:
            result = fut.result(timeout=self.tv.request_timeout_s)
        except TimeoutError:
            raise CdpUnreachable(
                f"bridge call {tool} timed out after {self.tv.request_timeout_s}s",
                hint="TradingView Desktop may be closed or the CDP port blocked; "
                     "relaunch with launch_tv_debug.bat and retry",
            ) from None

        texts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
        payload: dict = {}
        if texts:
            try:
                payload = json.loads(texts[0])
            except json.JSONDecodeError:
                payload = {"raw": texts[0]}

        if getattr(result, "isError", False) or payload.get("success") is False:
            msg = payload.get("error") or payload.get("raw") or "unknown bridge error"
            lowered = str(msg).lower()
            if "loading" in lowered or "could not extract" in lowered:
                raise ChartNotReady(
                    f"{tool}: {msg}",
                    hint="make sure the chart tab is open, on the right symbol, and finished loading",
                )
            if "connect" in lowered or "cdp" in lowered or "econnrefused" in lowered or "9222" in lowered:
                raise CdpUnreachable(
                    f"{tool}: {msg}",
                    hint="launch TradingView with --remote-debugging-port=9222 "
                         "(vendor/tradingview-mcp/scripts/launch_tv_debug.bat)",
                )
            raise DataSourceError(f"{tool}: {msg}")
        return payload

    # ── CandleSource API ──────────────────────────────────────────────────────
    def health_check(self) -> dict:
        state = self._call("tv_health_check")
        return {"source": self.name, **state}

    def _resolution_for(self, timeframe: str) -> str:
        try:
            return self.config.timeframes.tradingview_map[timeframe]
        except KeyError:
            raise UnsupportedTimeframe(
                f"timeframe {timeframe!r} has no TradingView mapping in config.yaml"
            ) from None

    def _chart_symbol_for(self, symbol: str) -> str:
        try:
            return self.config.symbols[symbol].chart_symbol
        except KeyError:
            raise SymbolMismatch(
                f"symbol {symbol!r} not defined in config.yaml symbols section"
            ) from None

    def ensure_chart(self, symbol: str, timeframe: str) -> None:
        """Point the chart at (symbol, timeframe) and VERIFY it took effect."""
        chart_symbol = self._chart_symbol_for(symbol)
        resolution = self._resolution_for(timeframe)
        want_root = _canonical_symbol_root(chart_symbol)

        state = self._call("chart_get_state")
        cur_symbol = str(state.get("symbol", ""))
        cur_root = _canonical_symbol_root(cur_symbol)
        cur_res = str(state.get("resolution", ""))

        if cur_root != want_root:
            self._call("chart_set_symbol", {"symbol": chart_symbol})
        if cur_res != resolution:
            self._call("chart_set_timeframe", {"timeframe": resolution})

        # Re-verify: never trust a set-call blindly.
        state = self._call("chart_get_state")
        got_symbol = str(state.get("symbol", ""))
        got_root = _canonical_symbol_root(got_symbol)
        got_res = str(state.get("resolution", ""))
        if got_root != want_root or got_res != resolution:
            raise SymbolMismatch(
                f"chart is on ({got_symbol}, {got_res}), wanted ({want_root}, {resolution}); "
                "refusing to read bars",
                hint="check the TradingView chart tab isn't being used manually mid-collection, "
                     "and that your data package covers this symbol",
            )

    def get_candles(self, symbol: str, timeframe: str, count: int) -> list[Candle]:
        if count < 1:
            raise NoBarsReturned("count must be >= 1")
        capped = min(count + 1, self.tv.max_bars_per_call)  # +1: last bar gets dropped as forming

        self.ensure_chart(symbol, timeframe)
        payload = self._call("data_get_ohlcv", {"count": capped, "summary": False})
        bars = payload.get("bars") or []
        if not bars:
            raise NoBarsReturned(
                f"bridge returned zero bars for ({symbol}, {timeframe})",
                hint="chart may still be loading; scroll the chart or wait and retry",
            )

        candles: list[Candle] = []
        for b in bars:
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    ts=_normalize_epoch_seconds(b["time"]),
                    open=float(b["open"]),
                    high=float(b["high"]),
                    low=float(b["low"]),
                    close=float(b["close"]),
                    volume=float(b.get("volume") or 0.0),
                    source="tvmcp",
                )
            )
        candles.sort(key=lambda c: c.ts)

        if self.config.data.collect.drop_unclosed_last_bar and candles:
            candles = candles[:-1]  # newest bar is still forming on a live chart
        return candles[-count:]

    def get_quote(self, symbol: str) -> dict:
        """Read the current 1m chart quote without storing the forming bar."""
        self.ensure_chart(symbol, self.config.data.collect.timeframe)
        payload = self._call("quote_get")
        for key in ("time",):
            if payload.get(key) is not None:
                payload[key] = _normalize_epoch_seconds(payload[key])
        return payload

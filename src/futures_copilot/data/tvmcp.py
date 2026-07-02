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
    "symbol_info",
}


def _normalize_epoch_seconds(t: float | int) -> int:
    """Bridge bar times come from TradingView internals; normalize ms -> s if needed."""
    t = int(t)
    return t // 1000 if t > 10_000_000_000 else t


def _symbol_root(chart_symbol: str) -> str:
    """'CME_MINI:MNQ1!' -> 'MNQ1!' for tolerant comparison with chart state."""
    return chart_symbol.split(":")[-1].strip().upper()


class TradingViewMcpCandleSource(CandleSource):
    name = "tvmcp"

    def __init__(self, config: Config):
        self.config = config
        self.tv = config.data.tvmcp
        self.server_path = config.resolve(self.tv.server_path)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session = None
        self._stack = None

    # ── connection management (background event loop, persistent session) ────
    def _ensure_connected(self) -> None:
        if self._session is not None:
            return
        if not self.server_path.exists():
            raise BridgeNotFound(
                f"MCP bridge not found at {self.server_path}",
                hint="clone tradesdontlie/tradingview-mcp into vendor/ and run `npm install` there "
                     "(see README: Setup step 2)",
            )
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        try:
            fut = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
            fut.result(timeout=self.tv.request_timeout_s)
        except DataSourceError:
            raise
        except Exception as e:  # connection-phase failures
            raise CdpUnreachable(
                f"could not start/connect MCP bridge: {e}",
                hint="is Node installed? is TradingView Desktop running with "
                     "--remote-debugging-port=9222? (use vendor/tradingview-mcp/scripts/launch_tv_debug.bat)",
            ) from e

    async def _connect(self) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()
        params = StdioServerParameters(
            command=self.tv.node_command,
            args=[str(self.server_path)],
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    def close(self) -> None:
        if self._loop is not None and self._stack is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._stack.aclose(), self._loop).result(timeout=10)
            except Exception:
                pass
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._session = None
        self._stack = None
        self._loop = None

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
        want_root = _symbol_root(chart_symbol)

        state = self._call("chart_get_state")
        cur_symbol = str(state.get("symbol", "")).upper()
        cur_res = str(state.get("resolution", ""))

        if want_root not in cur_symbol:
            self._call("chart_set_symbol", {"symbol": chart_symbol})
        if cur_res != resolution:
            self._call("chart_set_timeframe", {"timeframe": resolution})

        # Re-verify: never trust a set-call blindly.
        state = self._call("chart_get_state")
        got_symbol = str(state.get("symbol", "")).upper()
        got_res = str(state.get("resolution", ""))
        if want_root not in got_symbol or got_res != resolution:
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

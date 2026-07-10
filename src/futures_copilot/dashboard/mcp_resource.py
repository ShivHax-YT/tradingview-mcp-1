"""Session-scoped, self-healing TradingView MCP resource for Streamlit."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

import streamlit as st

from ..config import Config
from ..data.tvmcp import TradingViewMcpCandleSource
from ..errors import CdpUnreachable


class ResilientMcpResource:
    """Serialize access to one bridge and reconnect once after a transport drop."""

    name = "tvmcp-dashboard"

    def __init__(
        self,
        config: Config,
        source_factory: Callable[[Config], TradingViewMcpCandleSource] = TradingViewMcpCandleSource,
    ) -> None:
        self.config = config
        self._source_factory = source_factory
        self._source = source_factory(config)
        self._lock = threading.RLock()
        self._closed = False
        self._last_health_at = 0.0

    def _replace(self) -> None:
        self._source.close()
        self._source = self._source_factory(self.config)
        self._last_health_at = 0.0

    def _invoke(self, method: str, *args: Any) -> Any:
        with self._lock:
            if self._closed:
                raise CdpUnreachable("dashboard MCP resource is closed")
            try:
                return getattr(self._source, method)(*args)
            except CdpUnreachable:
                self._replace()
                return getattr(self._source, method)(*args)

    def health_check(self) -> dict:
        with self._lock:
            info = self._invoke("health_check")
            self._last_health_at = time.monotonic()
            return info

    def validate(self, max_age_s: float = 15.0) -> bool:
        if self._closed:
            return False
        if time.monotonic() - self._last_health_at <= max_age_s:
            return True
        try:
            self.health_check()
            return True
        except Exception:
            return False

    def get_candles(self, symbol: str, timeframe: str, count: int):
        return self._invoke("get_candles", symbol, timeframe, count)

    def assert_live_freshness(self, symbol: str, timeframe: str) -> None:
        self._invoke("assert_live_freshness", symbol, timeframe)

    def get_quote(self, symbol: str) -> dict:
        return self._invoke("get_quote", symbol)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._source.close()


def _validate_resource(resource: ResilientMcpResource) -> bool:
    return resource.validate()


def _release_resource(resource: ResilientMcpResource) -> None:
    resource.close()


@st.cache_resource(
    scope="session",
    validate=_validate_resource,
    on_release=_release_resource,
    show_spinner="Connecting to the TradingView MCP bridge...",
)
def session_mcp_source(config_key: str, _config: Config) -> ResilientMcpResource:
    """Return exactly one MCP bridge resource for this Streamlit session."""
    del config_key  # participates in the cache key; _config intentionally does not
    return ResilientMcpResource(_config)

from __future__ import annotations

from futures_copilot.dashboard.mcp_resource import ResilientMcpResource
from futures_copilot.errors import CdpUnreachable


class _FakeSource:
    def __init__(self, *, fail_quote: bool = False):
        self.fail_quote = fail_quote
        self.closed = False
        self.quote_calls = 0

    def health_check(self):
        return {"ok": True}

    def get_quote(self, symbol):
        self.quote_calls += 1
        if self.fail_quote:
            raise CdpUnreachable("transport dropped")
        return {"symbol": symbol, "last": 123.0}

    def get_candles(self, symbol, timeframe, count):
        return [(symbol, timeframe, count)]

    def close(self):
        self.closed = True


def test_resilient_resource_reuses_one_source(config):
    created = []

    def factory(_config):
        source = _FakeSource()
        created.append(source)
        return source

    resource = ResilientMcpResource(config, source_factory=factory)

    assert resource.get_quote("MNQ")["last"] == 123.0
    assert resource.get_candles("MNQ", "1m", 5) == [("MNQ", "1m", 5)]
    assert len(created) == 1
    resource.close()
    assert created[0].closed is True


def test_resilient_resource_reconnects_once_on_drop(config):
    created = []

    def factory(_config):
        source = _FakeSource(fail_quote=not created)
        created.append(source)
        return source

    resource = ResilientMcpResource(config, source_factory=factory)

    assert resource.get_quote("MNQ")["last"] == 123.0
    assert len(created) == 2
    assert created[0].closed is True
    assert created[1].quote_calls == 1

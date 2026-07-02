import json

import pytest

from futures_copilot.data.resample import resample_1m
from futures_copilot.errors import DataSourceError
from futures_copilot.features.market_state import build_market_state
from futures_copilot.models import Candle

from .feature_helpers import et_ts


def _candle(ts: int, o: float, h: float, l: float, c: float, v: float = 500.0) -> Candle:
    return Candle(symbol="MNQ", timeframe="1m", ts=ts, open=o, high=h, low=l,
                  close=c, volume=v, source="fixture")


def _seed(store):
    """Prior day bar + a NY morning (09:30-10:30) of 1m bars on 2026-06-24."""
    candles = [
        _candle(et_ts(2026, 6, 23, 10, 0), 23000, 23100, 22900, 23050),  # prior trading day
    ]
    start = et_ts(2026, 6, 24, 9, 30)
    price = 23000.0
    for i in range(61):  # 09:30 .. 10:30
        o = price
        c = price + (2 if i % 3 else -1)
        hi, lo = max(o, c) + 3, min(o, c) - 3
        candles.append(_candle(start + i * 60, o, hi, lo, c))
        price = c
    store.upsert_candles(candles)
    # Derived 5m/15m so ATR/structure inputs exist
    one_m = [c for c in candles[1:]]
    for tf in ("5m", "15m"):
        store.upsert_candles(resample_1m(one_m, tf))


def test_market_state_builds_and_serializes(config, store):
    _seed(store)
    state = build_market_state(store, config, "MNQ")

    assert state.symbol == "MNQ"
    assert state.trading_day == "2026-06-24"
    assert state.session == "ny"
    assert state.prior_day is not None
    assert state.prior_day.high == 23100 and state.prior_day.low == 22900
    assert state.opening_range is not None          # window elapsed by 10:30
    assert state.vwap is not None and state.vwap > 0
    assert state.vwap_position in ("above", "below")
    assert state.bar_counts["1m"] == 62
    assert state.day_range_position is not None and 0 <= state.day_range_position <= 1

    parsed = json.loads(state.to_json())            # valid JSON end to end
    assert parsed["symbol"] == "MNQ"


def test_market_state_as_of_replay(config, store):
    _seed(store)
    # As of 09:40 the opening range window (09:30-09:45) has NOT elapsed.
    as_of = et_ts(2026, 6, 24, 9, 40)
    state = build_market_state(store, config, "MNQ", as_of_ts=as_of)
    assert state.opening_range is None
    assert state.session == "ny"
    assert state.ts <= as_of                        # never uses future bars


def test_market_state_requires_bars(config, store):
    with pytest.raises(DataSourceError):
        build_market_state(store, config, "MNQ")

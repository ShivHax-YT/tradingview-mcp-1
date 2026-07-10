"""MarketState assembly — live-like data depth, honest levels, as-of replay.

Seeding mirrors production reality: deep history exists only on 15m (backfill),
1m exists only for recent windows. Levels must resolve from the finest COVERING
timeframe and must never be fabricated from partial coverage.
"""

import json

import pytest

from futures_copilot.data.resample import resample_1m
from futures_copilot.errors import DataSourceError
from futures_copilot.features.market_state import build_market_state
from futures_copilot.models import Candle

from .feature_helpers import et_ts


def _candle(tf: str, ts: int, o: float, h: float, l: float, c: float, v: float = 500.0) -> Candle:
    return Candle(symbol="MNQ", timeframe=tf, ts=ts, open=o, high=h, low=l,
                  close=c, volume=v, source="fixture")


def _seed(store):
    """Production-shaped data for trading day 2026-06-24:

    - 15m: full prior trading day 06-23 (06-22 18:00 -> 06-23 17:00, 92 bars,
      H=23100, L=22900) + current-day overnight (06-23 18:00 -> 06-24 09:30,
      62 bars; hi 23040 before midnight, hi 23080 / lo 22950 after) — like an
      actual 15m backfill.
    - 1m: a short overnight strip 22:50-23:00 (replay anchor) and the NY
      morning 09:30-10:30 — like a live 1m collect window.
    - 5m/15m resampled from the morning 1m, extending the seeded 15m history.
    """
    candles = []

    # prior trading day on 15m
    start = et_ts(2026, 6, 22, 18, 0)
    for i in range(92):
        hi = 23100.0 if i == 40 else 23020.0
        lo = 22900.0 if i == 60 else 22980.0
        candles.append(_candle("15m", start + i * 900, 23000, hi, lo, 23000, v=800))

    # current day overnight on 15m (62 bars: 18:00 -> 09:15 open, covers to 09:30)
    start = et_ts(2026, 6, 23, 18, 0)
    for i in range(62):
        if i < 24:                       # 18:00 - 00:00
            hi, lo = 23040.0, 22960.0
        elif i == 30:
            hi, lo = 23080.0, 22970.0    # day high made ~01:30
        elif i == 40:
            hi, lo = 23030.0, 22950.0    # day low made ~04:00 (london)
        else:
            hi, lo = 23030.0, 22970.0
        candles.append(_candle("15m", start + i * 900, 23000, hi, lo, 23000, v=800))

    # short overnight 1m strip (22:50 - 23:00) for as-of replay anchoring
    start = et_ts(2026, 6, 23, 22, 50)
    for i in range(11):
        candles.append(_candle("1m", start + i * 60, 23000, 23005, 22995, 23000))

    # NY morning 1m bars 09:30 - 10:30
    start = et_ts(2026, 6, 24, 9, 30)
    price = 23000.0
    morning = []
    for i in range(61):
        o = price
        c = price + (2 if i % 3 else -1)
        hi, lo = max(o, c) + 3, min(o, c) - 3
        morning.append(_candle("1m", start + i * 60, o, hi, lo, c))
        price = c
    candles.extend(morning)
    store.upsert_candles(candles)

    # derived 5m/15m from the morning 1m (like the collector's resample pass)
    for tf in ("5m", "15m"):
        store.upsert_candles(resample_1m(morning, tf))


def test_market_state_builds_and_serializes(config, store):
    _seed(store)
    state = build_market_state(store, config, "MNQ")

    assert state.symbol == "MNQ"
    assert state.trading_day == "2026-06-24"
    assert state.session == "ny"

    # prior day resolves from the 15m backfill (1m doesn't reach it)
    assert state.prior_day is not None
    assert state.prior_day.high == 23100 and state.prior_day.low == 22900
    assert state.level_sources["prior_day"] == "15m"

    # asia session resolves from 15m too
    asia = state.session_levels["asia"]
    assert asia is not None and asia.high == 23080 and asia.low == 22960
    assert state.level_sources["asia"] == "15m"

    # opening range is 1m-only and elapsed by 10:30
    assert state.opening_range is not None
    assert state.level_sources["opening_range"] == "1m"

    assert state.vwap is not None and state.vwap > 0
    assert state.vwap_position in ("above", "below")
    assert state.bar_counts["1m"] == 72          # 11 overnight + 61 morning
    assert state.day_range_position is not None and 0 <= state.day_range_position <= 1

    parsed = json.loads(state.to_json())         # valid JSON end to end
    assert parsed["symbol"] == "MNQ"


def test_lone_prior_day_bar_does_not_fabricate_pdh_pdl(config, store):
    """A single stray 1m bar inside the prior day must NOT produce prior-day
    levels — partial coverage returns None, never a plausible-looking lie."""
    candles = [_candle("1m", et_ts(2026, 6, 23, 10, 0), 23000, 23100, 22900, 23050)]
    start = et_ts(2026, 6, 24, 9, 30)
    for i in range(61):
        candles.append(_candle("1m", start + i * 60, 23000, 23010, 22990, 23005))
    store.upsert_candles(candles)

    state = build_market_state(store, config, "MNQ")
    assert state.prior_day is None
    assert state.level_sources["prior_day"] is None


def test_market_state_as_of_replay(config, store):
    _seed(store)
    # As of 09:40 the opening range window (09:30-09:45) has NOT elapsed.
    as_of = et_ts(2026, 6, 24, 9, 40)
    state = build_market_state(store, config, "MNQ", as_of_ts=as_of)
    assert state.opening_range is None
    assert state.session == "ny"
    assert state.ts <= as_of                     # never uses future bars


def test_day_range_position_preserves_source_precision(config, store, monkeypatch):
    _seed(store)
    monkeypatch.setattr(
        "futures_copilot.features.market_state.position_in_range",
        lambda *_args: 0.123456,
    )

    state = build_market_state(store, config, "MNQ")

    assert state.day_range_position == 0.123456


def test_as_of_replay_excludes_unclosed_higher_timeframe_bar(config, store):
    _seed(store)
    as_of = et_ts(2026, 6, 24, 10, 36)
    # This 15m bar opens before the replay horizon but does not close until
    # 10:45. Its impossible extreme must remain invisible at 10:37.
    store.upsert_candles([
        _candle("15m", et_ts(2026, 6, 24, 10, 30), 23000, 99999, 1, 23000),
    ])

    state = build_market_state(store, config, "MNQ", as_of_ts=as_of)

    assert state.session_levels["ny"] is not None
    assert state.session_levels["ny"].high < 99999
    assert state.session_levels["ny"].low > 1


def test_as_of_replay_no_future_leak_overnight(config, store):
    """Replayed at 23:00 during asia: the developing day range must be clipped
    to what existed then (hi 23040, NOT the 23080 made later at ~01:30), and
    windows that haven't happened yet must be None."""
    _seed(store)
    as_of = et_ts(2026, 6, 23, 23, 0)
    state = build_market_state(store, config, "MNQ", as_of_ts=as_of)

    assert state.session == "asia"
    assert state.trading_day == "2026-06-24"
    assert state.ts <= as_of

    # prior day is a completed past window — unaffected by the horizon
    assert state.prior_day is not None
    assert state.prior_day.high == 23100 and state.prior_day.low == 22900

    # sessions/windows that haven't started yet: None, not guesses
    assert state.session_levels["london"] is None
    assert state.session_levels["ny"] is None
    assert state.opening_range is None

    # developing asia/day levels reflect only bars known at 23:00
    asia = state.session_levels["asia"]
    assert asia is not None
    assert asia.high == 23040.0                  # 23080 print happens ~01:30 — future
    assert asia.low == 22960.0                   # 22950 print happens ~04:00 — future


def test_market_state_requires_bars(config, store):
    with pytest.raises(DataSourceError):
        build_market_state(store, config, "MNQ")

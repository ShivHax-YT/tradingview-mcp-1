from datetime import date
import json

from futures_copilot.features.market_state import MarketState, build_market_state
from futures_copilot.gate import evaluate
from futures_copilot.models import Candle
from futures_copilot.strategies.base import SignalCandidate
from futures_copilot.utils.roll_dates import get_next_roll_date, is_in_roll_window

from .feature_helpers import et_ts

EXPECTED_ROLL_REJECT_REASON = "Roll window active - contract rollover in progress"


def _candle(ts: int) -> Candle:
    return Candle(
        symbol="MNQ",
        timeframe="1m",
        ts=ts,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
        source="fixture",
    )


def _state(ts: int) -> MarketState:
    return MarketState(
        symbol="MNQ",
        ts=ts,
        as_of_close_ts=ts + 300,
        current_price=100.5,
        trading_day="2026-06-15",
        session="ny",
        session_levels={},
        prior_day=None,
        opening_range=None,
        level_sources={},
        vwap=None,
        vwap_position=None,
        atr_5m=None,
        atr_15m=None,
        premium_discount_day=None,
        day_range_position=None,
        last_swing_high_5m=None,
        last_swing_low_5m=None,
        open_fvgs_5m=[],
        bar_counts={},
        reliable=False,
        reason="roll_window",
    )


def _candidate(ts: int) -> SignalCandidate:
    return SignalCandidate(
        strategy="test",
        setup_type="mock_roll_signal",
        symbol="MNQ",
        direction="long",
        ts=ts,
        confirmed_close_ts=ts + 300,
        detection_timeframe="5m",
        session="ny",
        entry_lo=100.0,
        entry_hi=101.0,
        entry_ref=100.5,
        stop=99.0,
        target=103.0,
        target_name="mock_target",
        rr=2.5,
        grade="A",
    )


def test_get_next_roll_date_static_map_and_computed_fallback():
    assert get_next_roll_date(date(2026, 1, 1)) == date(2026, 3, 16)
    assert get_next_roll_date(date(2026, 3, 16)) == date(2026, 3, 16)
    # beyond the static table the calendar is COMPUTED, never None:
    assert get_next_roll_date(date(2028, 12, 12)) == date(2029, 3, 12)
    assert get_next_roll_date(date(2029, 1, 1)) == date(2029, 3, 12)


def test_computed_rolls_match_static_table():
    from futures_copilot.utils.roll_dates import EQUITY_INDEX_ROLL_DATES, _computed_rolls
    for year, rolls in EQUITY_INDEX_ROLL_DATES.items():
        assert _computed_rolls(year) == rolls


def test_2029_is_protected_by_computed_calendar():
    assert is_in_roll_window(date(2029, 3, 12))      # Mon before 3rd Fri Mar 2029
    assert is_in_roll_window(date(2029, 3, 9))
    assert not is_in_roll_window(date(2029, 3, 8))
    assert not is_in_roll_window(date(2029, 1, 1))   # unchanged from old suite


def test_roll_window_inclusive_boundaries():
    assert not is_in_roll_window(date(2026, 3, 12))
    assert is_in_roll_window(date(2026, 3, 13))
    assert is_in_roll_window(date(2026, 3, 16))
    assert is_in_roll_window(date(2026, 3, 19))
    assert not is_in_roll_window(date(2026, 3, 20))

    assert is_in_roll_window(date(2028, 12, 14))
    assert not is_in_roll_window(date(2028, 12, 15))
    assert not is_in_roll_window(date(2029, 1, 1))


def test_market_state_marks_roll_window_payload_unreliable(config, store):
    ts = et_ts(2026, 6, 15, 10, 0)
    store.upsert_candles([_candle(ts)])

    state = build_market_state(store, config, "MNQ")

    assert state.reliable is False
    assert state.reason == "roll_window"
    parsed = json.loads(state.to_json())
    assert parsed["reliable"] is False
    assert parsed["reason"] == "roll_window"


def test_risk_gate_rejects_roll_window_signal(config, store):
    ts = et_ts(2026, 6, 15, 10, 0)

    out = evaluate(_state(ts), [_candidate(ts)], store, config, persist=False)

    assert out.decision == "REJECT"
    assert len(out.evaluations) == 1
    gd = out.evaluations[0]
    assert gd.decision == "REJECT"
    assert gd.reasons == [EXPECTED_ROLL_REJECT_REASON]
    assert [check.check for check in gd.checklist] == ["roll_window_clear"]


def test_stale_feed_forces_wait_even_during_roll_window(config, store):
    ts = et_ts(2026, 6, 15, 10, 0)
    state = _state(ts)

    out = evaluate(
        state,
        [_candidate(ts)],
        store,
        config,
        persist=False,
        now_ts=state.as_of_close_ts + config.risk.max_feed_staleness_s + 1,
    )

    assert out.decision == "WAIT"
    assert out.evaluations[0].reasons == ["stale feed"]
    assert [check.check for check in out.evaluations[0].checklist] == ["feed_fresh"]


def test_risk_gate_uses_candidate_trading_day_after_18et(config, store):
    # Thu 20:05 ET belongs to Fri 06-12, the first day of the 06-15 roll
    # window. Raw calendar anchoring would incorrectly use Thu 06-11.
    ts = et_ts(2026, 6, 11, 20, 5)

    out = evaluate(_state(ts), [_candidate(ts)], store, config, persist=False)

    assert out.evaluations[0].reasons == [EXPECTED_ROLL_REJECT_REASON]


def test_risk_gate_uses_horizon_trading_day_after_18et(config, store):
    # The candidate was created before the rollover, but the human decision is
    # made after 18:00 ET on the next trading day inside the roll window.
    cand_ts = et_ts(2026, 6, 11, 16, 55)
    state = _state(cand_ts)
    state.as_of_close_ts = et_ts(2026, 6, 11, 18, 5)

    out = evaluate(state, [_candidate(cand_ts)], store, config, persist=False)

    assert out.evaluations[0].reasons == [EXPECTED_ROLL_REJECT_REASON]

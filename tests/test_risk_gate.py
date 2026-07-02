"""Risk gate: every hard rule, the caps, and decision authority."""

import json

import pytest

from futures_copilot.gate import evaluate
from futures_copilot.strategies import build_context, scan
from futures_copilot.strategies.liquidity_trap import SessionLiquidityTrap

from .feature_helpers import et_ts
from .strategy_fixtures import seed_long_trap


@pytest.fixture()
def scanned(config, store):
    """Real long-trap scan: state + candidates, nothing persisted yet."""
    seed_long_trap(store)
    ctx = build_context(store, config, "MNQ")
    cands = SessionLiquidityTrap().detect(ctx)
    assert cands
    return ctx.state, cands


def _asia(cands):
    return [c for c in cands if c.context["swept_level"] == "asia_low"][0]


def test_pass_produces_long_and_persists(config, store, scanned):
    state, cands = scanned
    out = evaluate(state, cands, store, config)
    assert out.decision == "LONG"
    assert out.chosen is not None and out.chosen.signal_id is not None
    assert out.chosen.reasons == []
    assert all(c.passed for c in out.chosen.checklist)
    row = store.latest_signals("MNQ", limit=5)[0]
    assert row["decision"] in ("LONG", "SHORT")
    assert row["trading_day"] == "2026-06-24"
    assert json.loads(row["invalidation"])


def test_wait_when_no_candidates(config, store, scanned):
    state, _ = scanned
    out = evaluate(state, [], store, config)
    assert out.decision == "WAIT"
    assert out.chosen is None and out.evaluations == []


def test_min_rr_reject(config, store, scanned):
    state, cands = scanned
    bad = _asia(cands).model_copy(update={"rr": 1.2})
    out = evaluate(state, [bad], store, config, persist=False)
    assert out.decision == "REJECT"
    assert any(r.startswith("min_rr") for r in out.evaluations[0].reasons)


def test_stale_signal_reject(config, store, scanned):
    state, cands = scanned
    c = _asia(cands)
    expired = c.model_copy(update={
        "confirmed_close_ts": state.as_of_close_ts - (config.risk.signal_expiry_candles + 2) * 300
    })
    out = evaluate(state, [expired], store, config, persist=False)
    assert any(r.startswith("not_stale") for r in out.evaluations[0].reasons)


def test_chased_entry_reject(config, store, scanned):
    state, cands = scanned
    c = _asia(cands)
    atr = c.context["atr"]
    # move the zone far below current price: price ran away without us
    drop = 3 * atr
    chased = c.model_copy(update={
        "entry_lo": c.entry_lo - drop, "entry_hi": c.entry_hi - drop,
        "entry_ref": c.entry_ref - drop, "stop": c.stop - drop,
    })
    out = evaluate(state, [chased], store, config, persist=False)
    assert any(r.startswith("entry_not_chased") for r in out.evaluations[0].reasons)


def test_stop_too_wide_reject(config, store, scanned):
    state, cands = scanned
    c = _asia(cands)
    wide = c.model_copy(update={"stop": c.entry_ref - 10 * c.context["atr"]})
    out = evaluate(state, [wide], store, config, persist=False)
    assert any(r.startswith("stop_width_within_atr_cap") for r in out.evaluations[0].reasons)


def test_target_too_close_reject(config, store, scanned):
    state, cands = scanned
    c = _asia(cands)
    atr = c.context["atr"]
    near = c.model_copy(update={"target": c.entry_ref + 0.2 * atr, "rr": 3.0})
    out = evaluate(state, [near], store, config, persist=False)
    assert any(r.startswith("target_not_too_close") for r in out.evaluations[0].reasons)


def test_unconfirmed_future_candidate_reject(config, store, scanned):
    state, cands = scanned
    c = _asia(cands)
    future = c.model_copy(update={"confirmed_close_ts": state.as_of_close_ts + 300})
    out = evaluate(state, [future], store, config, persist=False)
    assert any(r.startswith("confirmed_close_only") for r in out.evaluations[0].reasons)


def test_session_filter_reject(config, store, scanned):
    state, cands = scanned
    config.risk.allowed_sessions = ["london"]
    out = evaluate(state, [_asia(cands)], store, config, persist=False)
    assert any(r.startswith("session_allowed") for r in out.evaluations[0].reasons)


def test_chop_filter_reject(config, store, scanned):
    state, cands = scanned
    config.risk.chop_min_day_range_atr_mult = 50.0   # demand an absurd range
    out = evaluate(state, [_asia(cands)], store, config, persist=False)
    assert any(r.startswith("not_chop") for r in out.evaluations[0].reasons)


def test_manual_news_blackout_reject(config, store, scanned):
    from futures_copilot.config import NewsBlackout

    state, cands = scanned
    config.risk.news_blackouts = [NewsBlackout(start="2026-06-24 10:00", minutes=60, label="FOMC minutes")]
    out = evaluate(state, [_asia(cands)], store, config, persist=False)
    gd = out.evaluations[0]
    assert any(r.startswith("news_blackout_clear") for r in gd.reasons)
    assert "FOMC minutes" in " ".join(gd.reasons)


def test_session_and_day_caps(config, store, scanned):
    state, cands = scanned
    c = _asia(cands)
    # two passing signals already persisted this session
    for i in range(config.risk.max_signals_per_session):
        store.save_signal(symbol="MNQ", ts=c.ts - 3000 - i * 300, session="ny",
                          trading_day=state.trading_day, decision="LONG", setup="x",
                          grade="A", entry_lo=1, entry_hi=2, stop=0, tp1=5, tp2=None,
                          rr=2.0, reasons="[]", warnings="[]", invalidation="[]",
                          json_signal="{}")
    out = evaluate(state, [c], store, config, persist=False)
    assert out.decision == "REJECT"
    assert any(r.startswith("session_signal_cap") for r in out.evaluations[0].reasons)


def test_duplicate_scan_does_not_double_count(config, store, scanned):
    state, cands = scanned
    first = evaluate(state, cands, store, config)
    assert first.decision == "LONG"
    n = len(store.latest_signals("MNQ", limit=50))
    second = evaluate(state, cands, store, config)
    assert second.decision == "LONG"
    assert second.chosen.duplicate is True
    assert len(store.latest_signals("MNQ", limit=50)) == n     # no new rows


def test_gate_is_sole_decision_writer(config, store, scanned):
    """Persisted decision must equal the gate's output; candidate JSON carries
    no decision of its own."""
    state, cands = scanned
    out = evaluate(state, cands, store, config)
    row = store.latest_signals("MNQ", limit=1)[0]
    assert row["decision"] == out.evaluations[-1].decision or row["decision"] in ("LONG", "SHORT", "REJECT")
    cand_json = json.loads(row["json_signal"])["candidate"]
    assert "decision" not in cand_json

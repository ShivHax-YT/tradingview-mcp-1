"""Desk Mode v0.2: golden hour, trade governor, equal-level stop magnets,
IFVG evidence, session-prep cache. Python calculates, the gate decides,
Claude explains later, the human approves — these tests pin that down."""

import json

import pandas as pd
import pytest

from futures_copilot.config import RiskConfig
from futures_copilot.features.equal_levels import equal_level_near_stop
from futures_copilot.gate import evaluate
from futures_copilot.packet import packet_from_latest, render_markdown
from futures_copilot.prep import (
    VAULT_ALLOWLIST, build_session_prep, load_session_prep, write_session_prep,
)
from futures_copilot.strategies import build_context, scan
from futures_copilot.strategies.liquidity_trap import SessionLiquidityTrap

from .feature_helpers import et_ts
from .strategy_fixtures import (
    LONG_TRAP_15M, LONG_TRAP_5M, _seed_morning, seed_backdrop, seed_long_trap,
    seed_short_trap,
)


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


def _item(gate_decision, name):
    return next(c for c in gate_decision.checklist if c.check == name)


# ── config ───────────────────────────────────────────────────────────────────

def test_desk_mode_config_defaults():
    r = RiskConfig()
    assert r.enforce_golden_hour is True
    assert r.golden_hour == ["09:30", "11:00"]
    assert r.stop_on_first_win is True
    assert r.stop_after_losses == 2
    assert r.reject_equal_level_stop_magnets is True
    assert r.equal_level_tolerance_ticks == 3
    assert r.equal_level_lookback_bars == 50
    assert r.allowed_sessions == ["ny"], "Desk Mode default is NY only"


def test_desk_mode_config_loaded_from_yaml(config):
    r = config.risk
    assert r.enforce_golden_hour is True
    assert r.golden_hour == ["09:30", "11:00"]
    assert r.stop_on_first_win is True and r.stop_after_losses == 2
    assert r.reject_equal_level_stop_magnets is True
    assert r.allowed_sessions == ["ny"]
    assert config.vault.session_prep_dir  # prep cache location is configured


def test_golden_hour_config_validation():
    with pytest.raises(Exception):
        RiskConfig(golden_hour=["9am", "11am"])
    with pytest.raises(Exception):
        RiskConfig(golden_hour=["09:30"])


# ── golden hour ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("h,m,expected", [
    (9, 29, False),   # just before open — outside
    (9, 30, True),    # start is INCLUSIVE
    (10, 25, True),   # mid-window
    (10, 59, True),   # last minute inside
    (11, 0, False),   # end is EXCLUSIVE
    (11, 30, False),  # after — outside
])
def test_golden_hour_boundaries(config, store, scanned, h, m, expected):
    """The decision clock is the market state's as_of_close_ts (horizon)."""
    state, cands = scanned
    shifted = state.model_copy(update={"as_of_close_ts": et_ts(2026, 6, 24, h, m)})
    out = evaluate(shifted, [_asia(cands)], store, config, persist=False)
    item = _item(out.evaluations[0], "golden_hour_allowed")
    assert item.passed is expected, item.detail
    if not expected:
        assert any(r.startswith("golden_hour_allowed") for r in out.evaluations[0].reasons)


def test_golden_hour_not_enforced_passes_any_time(config, store, scanned):
    state, cands = scanned
    config.risk.enforce_golden_hour = False
    shifted = state.model_copy(update={"as_of_close_ts": et_ts(2026, 6, 24, 14, 0)})
    out = evaluate(shifted, [_asia(cands)], store, config, persist=False)
    assert _item(out.evaluations[0], "golden_hour_allowed").passed is True


def test_trap_still_passes_with_all_desk_checks(config, store, scanned):
    """The canonical long trap (horizon 10:25 ET) must still gate LONG: inside
    golden hour, governor clear, no stop magnet. Desk Mode adds checks, it does
    not break the known-good story."""
    state, cands = scanned
    out = evaluate(state, cands, store, config)
    assert out.decision == "LONG"
    chosen = out.chosen
    for name in ("golden_hour_allowed", "trade_governor_clear",
                 "stop_not_at_equal_liquidity"):
        item = _item(chosen, name)
        assert item.passed, f"{name} unexpectedly failed: {item.detail}"


# ── trade governor ───────────────────────────────────────────────────────────

def _attach_review(store, trading_day, *, taken, result_r, n=0):
    """Persist a REJECT signal row (does not consume session/day caps) and
    journal a review against it for the given trading day."""
    sid = store.save_signal(
        symbol="MNQ", ts=et_ts(2026, 6, 24, 9, 35) + n * 300, session="ny",
        trading_day=trading_day, decision="REJECT", setup="x", grade="B",
        entry_lo=1, entry_hi=2, stop=0, tp1=5, tp2=None, rr=2.0,
        reasons="[]", warnings="[]", invalidation="[]", json_signal="{}")
    store.add_trade_review(signal_id=sid, symbol="MNQ", taken=taken,
                           result_r=result_r)
    return sid


def test_governor_clear_with_no_reviews(config, store, scanned):
    state, cands = scanned
    out = evaluate(state, [_asia(cands)], store, config, persist=False)
    assert _item(out.evaluations[0], "trade_governor_clear").passed is True


def test_governor_one_win_rejects(config, store, scanned):
    state, cands = scanned
    _attach_review(store, state.trading_day, taken=True, result_r=1.8)
    out = evaluate(state, [_asia(cands)], store, config, persist=False)
    gd = out.evaluations[0]
    assert gd.decision == "REJECT"
    assert any(r.startswith("trade_governor_clear") for r in gd.reasons)
    assert "win" in _item(gd, "trade_governor_clear").detail


def test_governor_two_losses_reject_one_does_not(config, store, scanned):
    state, cands = scanned
    _attach_review(store, state.trading_day, taken=True, result_r=-1.0, n=1)
    out1 = evaluate(state, [_asia(cands)], store, config, persist=False)
    assert _item(out1.evaluations[0], "trade_governor_clear").passed is True

    _attach_review(store, state.trading_day, taken=True, result_r=-0.5, n=2)
    out2 = evaluate(state, [_asia(cands)], store, config, persist=False)
    gd = out2.evaluations[0]
    assert gd.decision == "REJECT"
    assert any(r.startswith("trade_governor_clear") for r in gd.reasons)


def test_governor_ignores_skips_and_scratches(config, store, scanned):
    state, cands = scanned
    _attach_review(store, state.trading_day, taken=False, result_r=None, n=1)  # skipped
    _attach_review(store, state.trading_day, taken=True, result_r=0.0, n=2)    # scratch
    _attach_review(store, state.trading_day, taken=True, result_r=None, n=3)   # open/undecided
    out = evaluate(state, [_asia(cands)], store, config, persist=False)
    assert _item(out.evaluations[0], "trade_governor_clear").passed is True


def test_governor_stop_on_first_win_can_be_disabled(config, store, scanned):
    state, cands = scanned
    config.risk.stop_on_first_win = False
    _attach_review(store, state.trading_day, taken=True, result_r=2.0)
    out = evaluate(state, [_asia(cands)], store, config, persist=False)
    assert _item(out.evaluations[0], "trade_governor_clear").passed is True


def test_day_trade_results_scoped_to_symbol_and_day(store):
    sid_today = store.save_signal(
        symbol="MNQ", ts=1, session="ny", trading_day="2026-06-24",
        decision="LONG", setup="x", grade="A", entry_lo=1, entry_hi=2, stop=0,
        tp1=5, tp2=None, rr=2.0, reasons="[]", warnings="[]", invalidation="[]",
        json_signal="{}")
    sid_prior = store.save_signal(
        symbol="MNQ", ts=2, session="ny", trading_day="2026-06-23",
        decision="LONG", setup="x", grade="A", entry_lo=1, entry_hi=2, stop=0,
        tp1=5, tp2=None, rr=2.0, reasons="[]", warnings="[]", invalidation="[]",
        json_signal="{}")
    store.add_trade_review(signal_id=sid_today, symbol="MNQ", taken=True, result_r=1.5)
    store.add_trade_review(signal_id=sid_prior, symbol="MNQ", taken=True, result_r=-1.0)
    store.add_trade_review(signal_id=None, symbol="MNQ", taken=True, result_r=3.0)
    res = store.day_trade_results("MNQ", "2026-06-24")
    assert res == [{"taken": True, "result_r": 1.5}]


# ── equal high/low stop magnets ─────────────────────────────────────────────

def _df(rows):
    """rows: (ts, high, low)."""
    return pd.DataFrame({
        "ts": [r[0] for r in rows],
        "open": [100.0] * len(rows),
        "high": [r[1] for r in rows],
        "low": [r[2] for r in rows],
        "close": [100.0] * len(rows),
    })


def test_equal_lows_helper_requires_two_matches():
    df = _df([(0, 110, 100.0), (300, 111, 105.0), (600, 112, 100.1)])
    hit = equal_level_near_stop(df, direction="long", stop=100.0, tolerance=0.25,
                                lookback_bars=50, horizon_ts=900, tf_seconds=300)
    assert hit is not None and hit.count == 2 and hit.side == "lows"

    one = _df([(0, 110, 100.0), (300, 111, 105.0), (600, 112, 104.0)])
    assert equal_level_near_stop(one, direction="long", stop=100.0, tolerance=0.25,
                                 lookback_bars=50, horizon_ts=900, tf_seconds=300) is None


def test_equal_highs_helper_short_side():
    df = _df([(0, 200.0, 190), (300, 195.0, 188), (600, 200.1, 189)])
    hit = equal_level_near_stop(df, direction="short", stop=200.0, tolerance=0.25,
                                lookback_bars=50, horizon_ts=900, tf_seconds=300)
    assert hit is not None and hit.side == "highs" and hit.count == 2


def test_equal_level_helper_no_lookahead():
    """A matching bar that has not CLOSED at the horizon must not count."""
    df = _df([(0, 110, 100.0), (300, 111, 105.0), (600, 112, 100.1)])
    # at horizon 600 only the first two bars are closed -> single match -> None
    assert equal_level_near_stop(df, direction="long", stop=100.0, tolerance=0.25,
                                 lookback_bars=50, horizon_ts=600, tf_seconds=300) is None
    # once the third bar closes (horizon 900) the cluster is knowable
    assert equal_level_near_stop(df, direction="long", stop=100.0, tolerance=0.25,
                                 lookback_bars=50, horizon_ts=900, tf_seconds=300) is not None


def test_equal_level_helper_respects_lookback():
    df = _df([(0, 110, 100.0), (300, 111, 100.1), (600, 112, 105.0), (900, 113, 106.0)])
    # both matches are older than the 2-bar lookback window
    assert equal_level_near_stop(df, direction="long", stop=100.0, tolerance=0.25,
                                 lookback_bars=2, horizon_ts=1200, tf_seconds=300) is None


def test_gate_rejects_long_stop_on_equal_lows(config, store, scanned):
    """LONG_TRAP_5M prints lows of exactly 22970 at 09:45 and 10:05 — a stop
    parked there sits inside resting sell-side liquidity."""
    state, cands = scanned
    magnet = _asia(cands).model_copy(update={"stop": 22970.0})
    out = evaluate(state, [magnet], store, config, persist=False)
    gd = out.evaluations[0]
    assert gd.decision == "REJECT"
    assert any(r.startswith("stop_not_at_equal_liquidity") for r in gd.reasons)
    assert "equal lows" in _item(gd, "stop_not_at_equal_liquidity").detail


def test_gate_rejects_short_stop_on_equal_highs(config, store):
    seed_short_trap(store)
    ctx = build_context(store, config, "MNQ")
    cands = SessionLiquidityTrap().detect(ctx)
    c = [x for x in cands if x.context["swept_level"] == "asia_high"][0]
    config.risk.equal_level_tolerance_ticks = 20        # 5.0 pts on MNQ
    magnet = c.model_copy(update={"stop": 23050.0})     # 23052 + 23048 highs nearby
    out = evaluate(ctx.state, [magnet], store, config, persist=False)
    gd = out.evaluations[0]
    assert any(r.startswith("stop_not_at_equal_liquidity") for r in gd.reasons)
    assert "equal highs" in _item(gd, "stop_not_at_equal_liquidity").detail


def test_equal_level_filter_can_be_disabled(config, store, scanned):
    state, cands = scanned
    config.risk.reject_equal_level_stop_magnets = False
    magnet = _asia(cands).model_copy(update={"stop": 22970.0})
    out = evaluate(state, [magnet], store, config, persist=False)
    item = _item(out.evaluations[0], "stop_not_at_equal_liquidity")
    assert item.passed is True and "not enforced" in item.detail


# ── IFVG inversion: evidence only ───────────────────────────────────────────

def test_ifvg_inversion_is_confluence_only(config, store):
    """A bearish FVG in the decline that a candle body closes through after the
    sweep adds evidence — and nothing else. No checklist item, no new gate."""
    bars5 = list(LONG_TRAP_5M)
    bars5[2] = (9, 40, 22980, 22984, 22974, 22976)   # bearish gap 22984-22988 vs 09:30 low
    seed_backdrop(store)
    _seed_morning(store, bars5, LONG_TRAP_15M)

    ctx = build_context(store, config, "MNQ")
    cands = SessionLiquidityTrap().detect(ctx)
    c = _asia(cands)
    assert "ifvg_inversion" in c.confluences
    assert c.context["ifvg_inversion"] is not None
    assert c.context["ifvg_inversion"]["inverted_ts"] <= ctx.horizon_ts

    out = evaluate(ctx.state, cands, store, config, persist=False)
    assert out.decision == "LONG"                    # evidence, not authority
    assert not any(chk.check.startswith("ifvg") for chk in out.chosen.checklist)


def test_ifvg_absent_on_plain_long_trap(config, store, scanned):
    _, cands = scanned
    c = _asia(cands)
    assert "ifvg_inversion" not in c.confluences
    assert c.context["ifvg_inversion"] is None


# ── session prep cache + packet ─────────────────────────────────────────────

def _fake_vault(tmp_path, count=len(VAULT_ALLOWLIST)):
    vault = tmp_path / "vault"
    for rel in VAULT_ALLOWLIST[:count]:
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {p.stem}\n\nDesk Mode context for {rel}.\n", encoding="utf-8")
    return vault


def test_session_prep_roundtrip_and_allowlist_only(config, tmp_path):
    vault = _fake_vault(tmp_path, count=3)           # two allowlisted files missing
    (vault / "99 Secret" ).mkdir(parents=True, exist_ok=True)
    (vault / "99 Secret" / "not_allowlisted.md").write_text("nope", encoding="utf-8")
    config.vault.path = str(vault)
    config.vault.session_prep_dir = str(tmp_path / "session_prep")

    prep = build_session_prep(config, "MNQ", "2026-06-24")
    path = write_session_prep(prep, config)
    assert path.exists() and path.name == "MNQ_2026-06-24.json"
    assert len(prep["notes"]) == 3 and len(prep["missing"]) == 2
    assert all(n["path"] in VAULT_ALLOWLIST for n in prep["notes"])
    assert not any("not_allowlisted" in n["path"] for n in prep["notes"])

    loaded = load_session_prep(config, "MNQ", "2026-06-24")
    assert loaded is not None and loaded["prep_schema"] == "copilot.session_prep.v1"
    assert load_session_prep(config, "MNQ", "2026-06-25") is None   # no cache, no error


def test_session_prep_truncates_large_notes(config, tmp_path):
    vault = _fake_vault(tmp_path, count=1)
    big = vault / VAULT_ALLOWLIST[0]
    big.write_text("x" * 10_000, encoding="utf-8")
    config.vault.path = str(vault)
    config.vault.max_chars_per_note = 500
    prep = build_session_prep(config, "MNQ", "2026-06-24")
    n = prep["notes"][0]
    assert n["truncated"] is True and len(n["content"]) == 500 and n["chars"] == 10_000


def test_session_prep_excludes_lifecycle_notes_and_captures_snapshot(config, tmp_path):
    vault = _fake_vault(tmp_path, count=3)
    (vault / VAULT_ALLOWLIST[0]).write_text(
        "\ufeff---\nstatus: deprecated\nexpires:\n---\nretired rule\n", encoding="utf-8",
    )
    (vault / VAULT_ALLOWLIST[1]).write_text(
        "---\nstatus: active\nexpires: 2000-01-01\n---\nexpired rule\n", encoding="utf-8",
    )
    config.vault.path = str(vault)
    config.vault.session_prep_dir = str(tmp_path / "session_prep")

    prep = build_session_prep(config, "MNQ", "2026-06-24")

    assert [n["path"] for n in prep["notes"]] == [VAULT_ALLOWLIST[2]]
    assert prep["notes"][0]["lifecycle"] == {"status": "active", "expires": ""}
    assert prep["excluded"] == [
        {"path": VAULT_ALLOWLIST[0], "reason": "status:deprecated"},
        {"path": VAULT_ALLOWLIST[1], "reason": "expired:2000-01-01"},
    ]


def test_load_session_prep_rechecks_cached_lifecycle_without_vault(config, tmp_path):
    config.vault.path = str(tmp_path / "vault-does-not-exist")
    config.vault.session_prep_dir = str(tmp_path / "session_prep")
    cached = {
        "symbol": "MNQ",
        "trading_day": "2026-06-24",
        "notes": [
            {"path": "active.md", "content": "active", "lifecycle": {"status": "active", "expires": ""}},
            {"path": "deprecated.md", "content": "old", "lifecycle": {"status": "deprecated", "expires": ""}},
            {"path": "expired.md", "content": "old", "lifecycle": {"status": "active", "expires": "2000-01-01"}},
            {"path": "legacy.md", "content": "old"},
        ],
        "excluded": [],
    }
    write_session_prep(cached, config)

    loaded = load_session_prep(config, "MNQ", "2026-06-24")

    assert loaded is not None
    assert [n["path"] for n in loaded["notes"]] == ["active.md"]
    assert loaded["excluded"] == [
        {"path": "deprecated.md", "reason": "status:deprecated"},
        {"path": "expired.md", "reason": "expired:2000-01-01"},
        {"path": "legacy.md", "reason": "lifecycle_snapshot_missing"},
    ]


FORBIDDEN_OUTPUT_FIELDS = ("decision", "action", "entry", "stop", "target", "size",
                           "execute", "order", "approve")


def test_packet_embeds_prep_cache_but_claude_still_cannot_decide(config, store, tmp_path):
    seed_long_trap(store)
    result = scan(store, config, "MNQ", persist=True)
    gate = evaluate(result.state, result.candidates, store, config, persist=True)
    assert gate.decision == "LONG"

    config.vault.path = str(_fake_vault(tmp_path))
    config.vault.session_prep_dir = str(tmp_path / "session_prep")
    write_session_prep(build_session_prep(config, "MNQ", "2026-06-24"), config)

    packet = packet_from_latest(store, config, "MNQ")
    assert packet["session_prep"] is not None
    assert len(packet["session_prep"]["notes"]) == len(VAULT_ALLOWLIST)
    # vault context never grants authority: output schema is unchanged
    schema = packet["claude_output_schema"]
    assert schema["additionalProperties"] is False
    for field in FORBIDDEN_OUTPUT_FIELDS:
        assert field not in schema["properties"]
    md = render_markdown(packet)
    assert "Session prep" in md


def test_packet_works_without_prep_cache(config, store, tmp_path):
    seed_long_trap(store)
    result = scan(store, config, "MNQ", persist=True)
    evaluate(result.state, result.candidates, store, config, persist=True)
    config.vault.session_prep_dir = str(tmp_path / "empty_prep")
    packet = packet_from_latest(store, config, "MNQ")
    assert packet["session_prep"] is None            # scan/gate/packet need no vault
    assert packet["risk_gate"]["decision"] == "LONG"


# ── dashboard desk status helper ────────────────────────────────────────────

def test_desk_mode_status_helper(config, store, tmp_path):
    pytest.importorskip("streamlit")
    from futures_copilot.dashboard import data as D

    seed_long_trap(store)
    result = scan(store, config, "MNQ", persist=True)
    evaluate(result.state, result.candidates, store, config, persist=True)
    config.vault.session_prep_dir = str(tmp_path / "prep")

    ov = D.load_overview(
        store, config, "MNQ", now_ts=result.state.as_of_close_ts,
    )
    desk = D.desk_mode_status(store, config, ov["state"])
    assert desk["golden_hour"]["within"] is True     # fixture horizon is 10:25 ET
    assert desk["governor"]["clear"] is True
    assert desk["prep"]["exists"] is False

    gv = D.gate_view(ov["latest_signal"])
    eq = D.checklist_item(gv, "stop_not_at_equal_liquidity")
    assert eq is not None and eq["passed"] is True
    assert D.desk_mode_status(store, config, None)["golden_hour"] is None

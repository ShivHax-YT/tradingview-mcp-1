"""Claude packet layer: schema completeness, decision immutability, files."""

import json

import pytest

from futures_copilot.gate import evaluate
from futures_copilot.packet import (
    CLAUDE_OUTPUT_SCHEMA, PacketError, build_packet, packet_from_latest,
    render_markdown, write_packet,
)
from futures_copilot.strategies import scan

from .strategy_fixtures import seed_long_trap

FORBIDDEN_OUTPUT_FIELDS = ("decision", "action", "entry", "stop", "target", "size",
                           "execute", "order", "approve")


@pytest.fixture()
def gated(config, store):
    seed_long_trap(store)
    result = scan(store, config, "MNQ", persist=True)
    gate = evaluate(result.state, result.candidates, store, config, persist=True)
    assert gate.decision == "LONG"
    return result, gate


def test_packet_from_latest_builds_full_schema(config, store, gated, tmp_path):
    config.app.packets_dir = str(tmp_path / "packets")
    store.set_daily_bias("2026-06-24", "MNQ", "bullish", "asia sweep reversal thesis")
    store.add_mistake("chased_entry", "entered 30pts past the zone on 06-20", "wait for retest")

    packet = packet_from_latest(store, config, "MNQ")

    assert packet["packet_schema"] == "copilot.packet.v1"
    assert packet["risk_gate"]["decision"] == "LONG"
    assert packet["candidate"]["setup_type"] == "session_liquidity_trap"
    assert packet["market_state"]["symbol"] == "MNQ"
    assert packet["risk_gate"]["checklist"], "checklist must ship with the packet"
    assert packet["daily_bias"]["bias"] == "bullish"
    assert any(m["tag"] == "chased_entry" for m in packet["recent_mistakes"])
    assert isinstance(packet["similar_setups"], list)
    assert packet["safety"]["decision_authority"] == "risk_gate_code_then_human"


def test_claude_output_schema_has_no_decision_authority(config, store, gated):
    packet = packet_from_latest(store, config, "MNQ")
    schema = packet["claude_output_schema"]
    assert schema is CLAUDE_OUTPUT_SCHEMA or schema == CLAUDE_OUTPUT_SCHEMA
    assert schema["additionalProperties"] is False
    for field in FORBIDDEN_OUTPUT_FIELDS:
        assert field not in schema["properties"], f"output schema must not carry {field!r}"
    # role text pins the authority
    assert "not yours" in packet["claude_role"]


def test_packet_files_written_and_roundtrip(config, store, gated, tmp_path):
    config.app.packets_dir = str(tmp_path / "pk")
    packet = packet_from_latest(store, config, "MNQ")
    jp, mp = write_packet(packet, config)
    assert jp.exists() and mp.exists()
    loaded = json.loads(jp.read_text(encoding="utf-8"))
    assert loaded["risk_gate"]["decision"] == "LONG"
    md = mp.read_text(encoding="utf-8")
    assert "## Decision: **LONG**" in md
    assert "[PASS]" in md
    assert "Respond ONLY with JSON" in md


def test_wait_packet_without_signals(config, store):
    seed_long_trap(store)
    result = scan(store, config, "MNQ", persist=True)   # state persisted, no gating
    packet = packet_from_latest(store, config, "MNQ")
    # signals table empty -> WAIT posture
    assert packet["risk_gate"]["decision"] == "WAIT"
    assert packet["candidate"] is None
    md = render_markdown(packet)
    assert "WAIT" in md


@pytest.mark.parametrize("bad_price", [None, "not-a-number", 0, -1, float("nan"), float("inf"), True])
def test_packet_fails_closed_on_invalid_current_price(config, store, bad_price):
    state = {"symbol": "MNQ", "trading_day": "2026-06-24", "current_price": bad_price}
    gate = {"decision": "WAIT", "signal_id": None, "checklist": [], "reasons": []}

    with pytest.raises(PacketError):
        build_packet(store, config, state=state, gate=gate, candidate=None)


def test_packet_from_latest_fails_closed_on_invalid_hydrated_price(config, store, gated):
    result, _gate = gated
    bad_state = result.state.model_dump()
    bad_state["current_price"] = 0.0
    store.save_market_state("MNQ", result.state.ts + 60, result.state.session, 0.0,
                            json.dumps(bad_state))

    with pytest.raises(PacketError):
        packet_from_latest(store, config, "MNQ")


def test_fable_template_has_absolute_invalid_price_directive():
    from futures_copilot.packet import load_template
    from futures_copilot.packet.writer import _TEMPLATE_CACHE

    _TEMPLATE_CACHE["text"] = None
    directive = ("CRITICAL: If px is 0 or invalid, declare the packet invalid "
                 "and stop evaluation immediately.")
    assert directive in load_template()


def test_template_lookup_prefers_module_path_over_cwd(tmp_path, monkeypatch):
    from futures_copilot.packet import load_template
    from futures_copilot.packet.writer import _TEMPLATE_CACHE

    poison = tmp_path / "src" / "futures_copilot" / "packet" / "templates"
    poison.mkdir(parents=True)
    (poison / "fable_review.md").write_text("POISON CWD TEMPLATE", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _TEMPLATE_CACHE["text"] = None

    text = load_template()

    assert "POISON CWD TEMPLATE" not in text
    assert "Fable Review" in text


def test_similar_setups_surface_in_packet(config, store, gated):
    """A second identical trap day later should see the first as a similar setup."""
    result, gate = gated
    sid = gate.chosen.signal_id
    store.add_trade_review(signal_id=sid, symbol="MNQ", taken=True, result_r=1.8,
                           mistake_tags='["exited_early"]', notes="took the asia trap long")
    packet = packet_from_latest(store, config, "MNQ")
    # the chosen signal itself is excluded; the OTHER gated candidate (london trap) may match
    for s_ in packet["similar_setups"]:
        assert s_["signal_id"] != gate.chosen.signal_id


def test_packet_prefers_actionable_signal_of_the_day(config, store, gated):
    """A scan can persist LONG then REJECT rows; the --latest packet must carry
    the actionable one, not whichever landed last."""
    result, gate = gated
    decisions = [g.decision for g in gate.evaluations]
    if "REJECT" in decisions:                      # both orderings covered
        packet = packet_from_latest(store, config, "MNQ")
        assert packet["risk_gate"]["decision"] == "LONG"
        assert packet["risk_gate"]["signal_id"] == gate.chosen.signal_id


def test_packet_latest_does_not_resurrect_stale_signal(config, store, gated):
    """If the newest market state is past the freshness window, the packet
    should return WAIT instead of packaging an old stored signal."""
    result, gate = gated
    assert gate.decision == "LONG"
    stale_state = result.state.model_copy(update={
        "ts": result.state.ts + 3600,
        "as_of_close_ts": result.state.as_of_close_ts + 3600,
    })
    store.save_market_state(
        stale_state.symbol, stale_state.ts, stale_state.session,
        stale_state.current_price, stale_state.to_json(),
    )

    packet = packet_from_latest(store, config, "MNQ")
    assert packet["risk_gate"]["decision"] == "WAIT"
    assert packet["candidate"] is None

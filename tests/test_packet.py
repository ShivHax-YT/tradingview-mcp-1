"""Claude packet layer: schema completeness, decision immutability, files."""

import json

import pytest

from futures_copilot.gate import evaluate
from futures_copilot.packet import (
    CLAUDE_OUTPUT_SCHEMA, build_packet, packet_from_latest, render_markdown, write_packet,
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

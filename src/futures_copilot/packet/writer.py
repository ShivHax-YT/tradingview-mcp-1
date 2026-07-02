"""Build and write Claude prompt packets (structured JSON + human Markdown).

The packet hands Claude everything Python computed — market state, the
candidate, the full risk checklist, the FINAL gate decision, similar setups,
recent mistakes, daily bias — and a strict output schema whose properties are
explanation/grading/warnings/journaling ONLY.

There is deliberately NO decision, action, entry, or execution field in the
output schema, and `additionalProperties: false` keeps it that way. Claude can
say "this A-grade long looks weak because X" — it cannot turn a WAIT into a
LONG, move a stop, or approve anything. That authority belongs to the risk
gate (code) and to the human (you).

No Anthropic API is called in v0.1: packets are written to disk and pasted
into Claude manually.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Config
from ..db.store import Store
from ..features.sessions import to_et
from ..memory import similar_setups

PACKET_SCHEMA_VERSION = "copilot.packet.v1"

CLAUDE_ROLE = (
    "You are the explanation layer of a MANUAL paper-trading copilot. "
    "Python computed everything below; the risk-gate decision is FINAL and is not yours "
    "to change, upgrade, or soften. Explain the setup in plain language, grade its quality, "
    "point out risks the checklist may understate, connect it to the trader's past mistakes "
    "and similar setups, and draft a journal note. The human approves or skips the trade — "
    "never you. If the decision is WAIT or REJECT, help the trader stay patient."
)

CLAUDE_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "copilot.claude_review.v1",
    "type": "object",
    "additionalProperties": False,          # nothing sneaks in — especially not a decision
    "required": ["explanation", "setup_quality", "warnings", "journal_note"],
    "properties": {
        "explanation": {"type": "string", "description": "Plain-language story of what the chart did and why the gate decided what it decided."},
        "setup_quality": {"type": "string", "enum": ["A", "B", "C", "D", "F"], "description": "Claude's independent read of setup QUALITY. Grading commentary only — has no effect on the decision."},
        "warnings": {"type": "array", "items": {"type": "string"}, "description": "Risks/red flags worth a human glance."},
        "mistake_echoes": {"type": "array", "items": {"type": "string"}, "description": "Which of the trader's logged mistakes this setup could repeat."},
        "journal_note": {"type": "string", "description": "Draft journal entry for the trading brain."},
        "questions_for_trader": {"type": "array", "items": {"type": "string"}},
    },
    "note": "There is intentionally no decision/action/entry/size field. The risk gate already decided.",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_ts(ts: int | None) -> str:
    if ts is None:
        return "—"
    return to_et(int(ts)).strftime("%Y-%m-%d %H:%M ET")


def build_packet(
    store: Store, config: Config, *,
    state: dict[str, Any],
    gate: dict[str, Any],
    candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble the packet dict. `state`/`gate`/`candidate` are plain dicts so
    the builder works both live (from a scan) and offline (from DB rows)."""
    symbol = state.get("symbol", "MNQ")
    trading_day = state.get("trading_day")

    similar = []
    if candidate is not None:
        similar = similar_setups(
            store, symbol=symbol, setup=candidate.get("setup_type", ""),
            direction=candidate.get("direction", ""), session=candidate.get("session"),
            grade=candidate.get("grade"), rr=candidate.get("rr"),
            sweep_level=(candidate.get("context") or {}).get("swept_level"),
            exclude_signal_id=gate.get("signal_id"),
        )

    bias = store.get_daily_bias(trading_day, symbol) if trading_day else None

    return {
        "packet_schema": PACKET_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "safety": {
            "mode": "manual_paper_trading_decision_support",
            "decision_authority": "risk_gate_code_then_human",
            "claude_may": ["explain", "grade_quality", "warn", "journal"],
            "claude_may_not": ["decide", "change_levels", "approve", "execute", "alert"],
        },
        "claude_role": CLAUDE_ROLE,
        "market_state": state,
        "candidate": candidate,
        "risk_gate": gate,
        "daily_bias": bias,
        "recent_mistakes": store.list_mistakes(limit=10),
        "similar_setups": similar,
        "claude_output_schema": CLAUDE_OUTPUT_SCHEMA,
    }


def render_markdown(packet: dict[str, Any]) -> str:
    """Human/Claude-pasteable Markdown view of the packet."""
    st = packet["market_state"]
    gate = packet["risk_gate"]
    cand = packet["candidate"]
    lines: list[str] = []
    a = lines.append

    a(f"# Copilot packet — {st.get('symbol')} {st.get('trading_day')}")
    a("")
    a(f"*Generated {packet['generated_at']} · schema {packet['packet_schema']} · "
      f"manual paper trading — the gate decision below is final; Claude explains only.*")
    a("")
    a(f"## Decision: **{gate.get('decision')}**")
    if gate.get("reasons"):
        a("")
        for r in gate["reasons"]:
            a(f"- REJECT reason: {r}")
    a("")
    a("## Market state")
    a("")
    a(f"- price **{st.get('current_price')}** · session **{st.get('session')}** · "
      f"as of {_fmt_ts(st.get('ts'))}")
    pd_ = st.get("prior_day")
    if pd_:
        a(f"- prior day: H {pd_.get('high')} / L {pd_.get('low')}")
    for name, lp in (st.get("session_levels") or {}).items():
        if lp:
            a(f"- {name}: H {lp.get('high')} / L {lp.get('low')}")
    orng = st.get("opening_range")
    if orng:
        a(f"- opening range: H {orng.get('high')} / L {orng.get('low')}")
    a(f"- VWAP {st.get('vwap')} ({st.get('vwap_position')}) · ATR5 {st.get('atr_5m')} · "
      f"day range position {st.get('day_range_position')} ({st.get('premium_discount_day')})")
    a("")
    if cand:
        a("## Candidate")
        a("")
        a(f"- **{cand.get('setup_type')}** {cand.get('direction', '').upper()} · grade {cand.get('grade')} "
          f"· {cand.get('detection_timeframe')} · confirmed {_fmt_ts(cand.get('confirmed_close_ts'))}")
        a(f"- entry zone {cand.get('entry_lo')}–{cand.get('entry_hi')} (ref {cand.get('entry_ref')}) · "
          f"stop {cand.get('stop')} · target {cand.get('target')} ({cand.get('target_name')}) · "
          f"RR {cand.get('rr'):.2f}" if cand.get("rr") is not None else "- geometry incomplete")
        ctx = cand.get("context") or {}
        a(f"- swept **{ctx.get('swept_level')}** @ {ctx.get('swept_price')} "
          f"(extreme {ctx.get('sweep_extreme')}), reclaimed in {ctx.get('reclaim_candles')} candle(s), "
          f"confirmed via {ctx.get('confirmation_kind')}")
        if cand.get("confluences"):
            a(f"- confluences: {', '.join(cand['confluences'])}")
        if cand.get("warnings"):
            a(f"- strategy warnings: {', '.join(cand['warnings'])}")
        a("")
    a("## Risk checklist")
    a("")
    for c in gate.get("checklist", []):
        mark = "PASS" if c.get("passed") else "FAIL"
        a(f"- [{mark}] {c.get('check')} — {c.get('detail')}")
    if gate.get("invalidation"):
        a("")
        a("Invalidation:")
        for i in gate["invalidation"]:
            a(f"- {i}")
    a("")
    bias = packet.get("daily_bias")
    a("## Daily bias")
    a("")
    a(f"- {bias['bias']}: {bias['notes']}" if bias else "- none journaled for this trading day")
    a("")
    a("## Recent mistakes to not repeat")
    a("")
    if packet["recent_mistakes"]:
        for m in packet["recent_mistakes"]:
            a(f"- **{m['tag']}** — {m['description']}" + (f" (rule: {m['rule_update']})" if m.get("rule_update") else ""))
    else:
        a("- none logged yet")
    a("")
    a("## Similar past setups")
    a("")
    if packet["similar_setups"]:
        for s_ in packet["similar_setups"]:
            oc = s_.get("outcome") or {}
            oc_txt = (f"taken, {oc.get('result_r')}R" if oc.get("taken") else "not taken") if oc else "no review"
            a(f"- #{s_['signal_id']} {s_['trading_day']} {s_['session']} {s_['decision']} "
              f"grade {s_['grade']} rr {s_['rr']} swept {s_['swept_level']} → {oc_txt}")
    else:
        a("- none on record yet")
    a("")
    a("## Your task, Claude")
    a("")
    a(packet["claude_role"])
    a("")
    a("Respond ONLY with JSON matching `claude_output_schema` from the JSON packet "
      "(explanation, setup_quality, warnings, mistake_echoes, journal_note, questions_for_trader). "
      "It has no decision field on purpose.")
    a("")
    return "\n".join(lines)


def write_packet(packet: dict[str, Any], config: Config) -> tuple[Path, Path]:
    out_dir = config.resolve(config.app.packets_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    st = packet["market_state"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sid = (packet["risk_gate"] or {}).get("signal_id")
    base = f"packet_{stamp}_{st.get('symbol','X')}" + (f"_sig{sid}" if sid else "")
    jp = out_dir / f"{base}.json"
    mp = out_dir / f"{base}.md"
    jp.write_text(json.dumps(packet, indent=2, default=str), encoding="utf-8")
    mp.write_text(render_markdown(packet), encoding="utf-8")
    return jp, mp


def packet_from_latest(store: Store, config: Config, symbol: str) -> dict[str, Any]:
    """Rebuild a packet from the newest persisted state + signal (offline path).
    Falls back to a WAIT packet when no signal exists yet."""
    ms = store.latest_market_state(symbol)
    if ms is None:
        raise ValueError(f"no market state stored for {symbol}; run `copilot scan` first")
    state = json.loads(ms["json_state"])

    sigs = store.latest_signals(symbol, limit=1)
    if not sigs:
        gate = {"decision": "WAIT", "signal_id": None, "checklist": [], "reasons": [],
                "warnings": [], "invalidation": []}
        return build_packet(store, config, state=state, gate=gate, candidate=None)

    row = sigs[0]
    js = json.loads(row["json_signal"])
    gate = {
        "decision": row["decision"], "signal_id": row["id"],
        "checklist": js.get("checklist", []),
        "reasons": json.loads(row["reasons"] or "[]"),
        "warnings": json.loads(row["warnings"] or "[]"),
        "invalidation": json.loads(row["invalidation"] or "[]"),
    }
    return build_packet(store, config, state=state, gate=gate, candidate=js.get("candidate"))

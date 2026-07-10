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

The Claude-facing prompt lives in an external template
(`templates/fable_review.md`) — writer.py holds no prompt text. The template
receives a MINIFIED context block (`sym`/`px`/`act`/`rr`/`v_rules`) so the
token cost of a review stays flat regardless of how much the full packet grows.

No Anthropic API is called in v0.1: packets are written to disk and pasted
into Claude manually.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Config
from ..db.store import Store
from ..features.sessions import to_et
from ..memory import similar_setups
from ..prep import load_session_prep
from ..preflight import load_preflight
from ..strategies.liquidity_trap import TF_SECONDS

PACKET_SCHEMA_VERSION = "copilot.packet.v1"

# Claude prompt template (external file — single source of truth for the role).
PACKET_TEMPLATE_FILE = Path("src/futures_copilot/packet/templates/fable_review.md")
MINIFIED_PACKET_PLACEHOLDER = "{{MINIFIED_PACKET}}"
_TEMPLATE_CACHE: dict[str, str | None] = {"text": None}

# Decimal places for floats in every exported payload — FIELD-AWARE:
# prices/ATRs/R-multiples on 0.25-tick instruments are exact at 2 dp;
# 0-1 fractions need 4 dp (2 dp turns 0.125 into 0.12 and hides the
# premium/discount midline). Ratio fields are matched by exact key or by
# suffix at ANY nesting depth; extend _RATIO_KEYS when new fraction fields
# join the packet.
_PRICE_DECIMALS = 2
_RATIO_DECIMALS = 4
_RATIO_KEYS = frozenset({"day_range_position"})
_RATIO_KEY_SUFFIXES = ("_position", "_ratio", "_fraction", "_pct")
# Failed-rule lists are sliced to this many items in the minified context.
_MAX_VIOLATED_RULES = 2

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


def load_template() -> str:
    """Read the Fable review prompt template (cached after first read).

    Reads the spec'd repo-relative path when running from the project root;
    falls back to a module-relative path so installed/`streamlit run`/other-cwd
    invocations resolve the same file.
    """
    if _TEMPLATE_CACHE["text"] is None:
        if PACKET_TEMPLATE_FILE.exists():
            template_text = Path("src/futures_copilot/packet/templates/fable_review.md").read_text(encoding="utf-8")
        else:
            template_text = (Path(__file__).resolve().parent / "templates" / "fable_review.md").read_text(encoding="utf-8")
        _TEMPLATE_CACHE["text"] = template_text
    return _TEMPLATE_CACHE["text"]


def _decimals_for(key: str | None) -> int:
    if key and (key in _RATIO_KEYS or key.endswith(_RATIO_KEY_SUFFIXES)):
        return _RATIO_DECIMALS
    return _PRICE_DECIMALS


def _round2(value: Any) -> float:
    """Best-effort 2-decimal float; 0.0 for missing/non-numeric values."""
    try:
        return round(float(value), _PRICE_DECIMALS)
    except (TypeError, ValueError):
        return 0.0


def _round_floats(obj: Any, key: str | None = None) -> Any:
    """Recursively round floats: prices to 2 dp, ratio fields to 4 dp.

    The owning dict key travels down into lists/tuples, so a list of
    fractions under a ratio-named key keeps ratio precision."""
    if isinstance(obj, bool):               # bool is an int subclass — leave it
        return obj
    if isinstance(obj, float):
        return round(obj, _decimals_for(key))
    if isinstance(obj, dict):
        return {k: _round_floats(v, k) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_floats(v, key) for v in obj]
    return obj


def minify_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Compress a full packet into the tight Claude-facing context block.

    Exact schema: {"sym": str, "px": float, "act": str, "rr": float,
    "v_rules": list}. Floats are rounded to 2 decimals and the violated-rule
    list is sliced to the top 2 entries to minimize context-window bloat.
    """
    state = packet.get("market_state") or {}
    gate = packet.get("risk_gate") or {}
    cand = packet.get("candidate") or {}

    violated: list[str] = [str(r) for r in (gate.get("reasons") or [])]
    violated += [str(c.get("check")) for c in (gate.get("checklist") or [])
                 if not c.get("passed") and c.get("check")]
    seen: set[str] = set()
    v_rules = [r for r in violated if not (r in seen or seen.add(r))]

    return {
        "sym": str(state.get("symbol", "")),
        "px": _round2(state.get("current_price")),
        "act": str(gate.get("decision", "WAIT")),
        "rr": _round2(cand.get("rr")),
        "v_rules": v_rules[:_MAX_VIOLATED_RULES],
    }


def render_claude_prompt(packet: dict[str, Any]) -> str:
    """Template + minified context = the exact text Claude reviews."""
    mini = packet.get("claude_packet_min") or minify_packet(packet)
    blob = json.dumps(mini, separators=(",", ":"), default=str)
    template_text = load_template()
    if MINIFIED_PACKET_PLACEHOLDER in template_text:
        return template_text.replace(MINIFIED_PACKET_PLACEHOLDER, blob)
    return f"{template_text.rstrip()}\n\n{blob}\n"


def export_packet_json(packet: dict[str, Any]) -> str:
    """Whitespace-stripped, float-rounded JSON for disk/download export."""
    return json.dumps(_round_floats(packet), separators=(",", ":"), default=str)


_HASH_EXCLUDED_KEYS = frozenset({"generated_at", "content_hash"})


def packet_content_hash(packet: dict[str, Any]) -> str:
    """Deterministic SHA-256 of the packet payload, minus volatile metadata.

    Two packets built from identical inputs hash identically regardless of
    WHEN they were built; any change anywhere in the inputs — an edited
    review note under the journal upsert (same row id, same row count), a
    retro candle backfill that rewrites VWAP at the same state ts, a bias-
    note edit, the 13th mistake past a limit-12 list, a new prep/preflight
    cache — changes the hash, because it changes the payload. Freshness by
    construction, not by enumerating invalidation triggers."""
    payload = {k: v for k, v in packet.items() if k not in _HASH_EXCLUDED_KEYS}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


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

    # Session-prep vault cache: read-if-present local JSON written by
    # `copilot prep`. Never triggers a vault read here — packets must build
    # (and the gate must decide) with or without Obsidian.
    session_prep = load_session_prep(config, symbol, trading_day)

    # Desk Memory / Preflight cache: deterministic journal statistics written
    # by `copilot preflight`. Read-if-present, like session_prep — reminders
    # for Claude to echo, never authority.
    preflight_memory = load_preflight(config, symbol, trading_day)

    packet = {
        "packet_schema": PACKET_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "safety": {
            "mode": "manual_paper_trading_decision_support",
            "decision_authority": "risk_gate_code_then_human",
            "claude_may": ["explain", "grade_quality", "warn", "journal"],
            "claude_may_not": ["decide", "change_levels", "approve", "execute", "alert"],
        },
        "claude_role": load_template(),
        "market_state": state,
        "candidate": candidate,
        "risk_gate": gate,
        "daily_bias": bias,
        "recent_mistakes": store.list_mistakes(limit=10),
        "similar_setups": similar,
        "session_prep": session_prep,          # vault context cache, or None
        "preflight_memory": preflight_memory,  # journal memory cache, or None
        "claude_output_schema": CLAUDE_OUTPUT_SCHEMA,
    }
    # The tight Claude-facing block travels WITH the archival packet so every
    # consumer (dashboard, files, future API calls) minifies identically.
    packet["claude_packet_min"] = minify_packet(packet)
    # Content-addressed identity: consumers (dashboard cache, Obsidian
    # ingestion, file dedupe) use this to detect REAL changes.
    packet["content_hash"] = packet_content_hash(packet)
    return packet


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
    mem = packet.get("preflight_memory")
    a("## Desk Memory / Preflight")
    a("")
    if mem:
        perf = mem.get("performance") or {}
        a(f"- last {perf.get('total_reviewed', 0)} reviews: {perf.get('wins', 0)}W / "
          f"{perf.get('losses', 0)}L / {perf.get('scratches', 0)} scratch · "
          f"{perf.get('skipped', 0)} skipped"
          + (f" · avg {perf['avg_result_r']:+.2f}R on taken"
             if perf.get("avg_result_r") is not None else ""))
        for t in (mem.get("top_mistake_tags") or [])[:5]:
            a(f"- repeated mistake: **{t.get('tag')}** x{t.get('count')}")
        for b in (mem.get("packet_bullets") or [])[:8]:
            a(f"- {b}")
        a("- (deterministic journal memory — reminders only, zero decision authority)")
    else:
        a("- no desk-memory cache for this day — run `copilot preflight` before "
          "the session (optional)")
    a("")
    prep = packet.get("session_prep")
    a("## Session prep (vault context)")
    a("")
    if prep and prep.get("notes"):
        a(f"- cached {prep.get('generated_at')} from {len(prep['notes'])} allowlisted note(s); "
          "full text is in the JSON packet under `session_prep`")
        for n in prep["notes"]:
            a(f"- **{n.get('title')}** ({n.get('path')})")
    else:
        a("- no session prep cache for this day — run `copilot prep` before the session (optional)")
    a("")
    a("## Claude prompt (do not paste this file)")
    a("")
    a("This markdown is the ARCHIVAL record of the packet. The Claude-facing")
    a("prompt is the external template + minified context ONLY. It is written")
    a("alongside this file as `*.prompt.txt` and shown in the dashboard's")
    a("Packet tab ('paste THIS' block). Pasting this whole file into Claude")
    a("hands it contradictory context: the sections above cite ATR/VWAP and")
    a("multi-day levels that the template explicitly forbids mentioning.")
    a("")
    a("The prompt file already instructs Claude to Respond ONLY with JSON "
      "matching `claude_output_schema` (explanation, setup_quality, warnings, "
      "mistake_echoes, journal_note, questions_for_trader) — no decision "
      "field, on purpose.")
    a("")
    return "\n".join(lines)


def write_packet(packet: dict[str, Any], config: Config) -> tuple[Path, Path]:
    """Write the archival JSON + Markdown views AND the paste-ready prompt.

    Returns (json_path, md_path) — signature unchanged for existing callers
    and tests. The third artifact, `<base>.prompt.txt`, lands next to them
    and is the ONLY file meant to be pasted into Claude: external template +
    minified context, nothing else."""
    out_dir = config.resolve(config.app.packets_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    st = packet["market_state"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sid = (packet["risk_gate"] or {}).get("signal_id")
    base = f"packet_{stamp}_{st.get('symbol','X')}" + (f"_sig{sid}" if sid else "")
    jp = out_dir / f"{base}.json"
    mp = out_dir / f"{base}.md"
    pp = out_dir / f"{base}.prompt.txt"
    jp.write_text(export_packet_json(packet), encoding="utf-8")
    mp.write_text(render_markdown(packet), encoding="utf-8")
    pp.write_text(render_claude_prompt(packet), encoding="utf-8")
    return jp, mp


def _wait_packet(store: Store, config: Config, state: dict[str, Any]) -> dict[str, Any]:
    gate = {"decision": "WAIT", "signal_id": None, "checklist": [], "reasons": [],
            "warnings": [], "invalidation": []}
    return build_packet(store, config, state=state, gate=gate, candidate=None)


def _signal_is_current(row: dict[str, Any], state: dict[str, Any], config: Config) -> bool:
    """Return whether a stored signal is still fresh for the newest market state."""
    if row["trading_day"] != state.get("trading_day"):
        return False
    try:
        js = json.loads(row["json_signal"])
        cand = js.get("candidate") or {}
        confirmed = int(cand["confirmed_close_ts"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    horizon = int(state.get("as_of_close_ts") or state.get("ts") or 0)
    tf_s = TF_SECONDS.get(cand.get("detection_timeframe", "5m"), 300)
    return horizon <= confirmed + config.risk.signal_expiry_candles * tf_s


def packet_from_latest(store: Store, config: Config, symbol: str) -> dict[str, Any]:
    """Rebuild a packet from the newest persisted state + signal (offline path).
    Falls back to a WAIT packet when no signal exists yet."""
    ms = store.latest_market_state(symbol)
    if ms is None:
        raise ValueError(f"no market state stored for {symbol}; run `copilot scan` first")
    state = json.loads(ms["json_state"])

    sigs = store.latest_signals(symbol, limit=10)
    if not sigs:
        return _wait_packet(store, config, state)
    sigs = [r for r in sigs if _signal_is_current(r, state, config)]
    if not sigs:
        return _wait_packet(store, config, state)

    # Prefer the newest ACTIONABLE signal of the same trading day (a scan can
    # persist a passing signal and then rejects; the human wants the packet for
    # the one that matters). Fall back to the newest row (REJECT packets are
    # legitimate — they explain why you're standing down).
    newest = sigs[0]
    passing = [r for r in sigs
               if r["decision"] in ("LONG", "SHORT") and r["trading_day"] == newest["trading_day"]]
    row = passing[0] if passing else newest
    js = json.loads(row["json_signal"])
    gate = {
        "decision": row["decision"], "signal_id": row["id"],
        "checklist": js.get("checklist", []),
        "reasons": json.loads(row["reasons"] or "[]"),
        "warnings": json.loads(row["warnings"] or "[]"),
        "invalidation": json.loads(row["invalidation"] or "[]"),
    }
    return build_packet(store, config, state=state, gate=gate, candidate=js.get("candidate"))

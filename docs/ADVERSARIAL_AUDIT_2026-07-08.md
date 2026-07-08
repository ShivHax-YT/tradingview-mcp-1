# Adversarial Architecture Audit — Futures AI Chart Copilot
2026-07-08 · audited at HEAD (v0.2 Desk Mode + v0.3 Desk Memory, 199 tests green) · uploads verified byte-identical to repo files

| # | Domain | Verdict | Severity |
|---|--------|---------|----------|
| 1 | Packet context contradiction | CONFIRMED — md embeds a template that forbids content the same md contains | HIGH |
| 2 | Roll-date temporal boundaries | CONFIRMED — gate anchors to calendar date, not trading day; 2029+ silently unprotected | HIGH |
| 3 | Packet cache invalidation | CONFIRMED — 4 distinct stale-cache vectors | MEDIUM |
| 4 | Precision loss in export | CONFIRMED — `day_range_position` crushed to 2 dp | LOW |
| 5 | SQLite locks / dispatch lockups | SQLite hypothesis DISPROVEN by crash simulation; REAL leak found in the MCP bridge lifecycle | HIGH |
| 6 | Obsidian 2nd-brain pollution | No purge protocol existed — blueprint + code guard provided | MEDIUM |

All `file:line` references are against the current working tree (uploads == repo, verified with `diff`).

---

## DOMAIN 1 — PACKET CONTEXT CONTRADICTION (THE "JAEGER FIX")

### Finding 1.1 — The full markdown feeds Claude data the embedded template forbids it to mention

**File:Line:**
- `src/futures_copilot/packet/writer.py:247-248` — `render_markdown()` prints `VWAP {…} · ATR5 {…} · day range position {…}` into the document.
- `src/futures_copilot/packet/writer.py:332-338` — the same document then embeds the full Claude prompt via `a(render_claude_prompt(packet))` under `## Your task, Claude`.
- `src/futures_copilot/packet/templates/fable_review.md:27-29` — that embedded template says: *"Never mention ATR, VWAP, or any metric absent from the packet"* and (lines 23-25) that every claim must trace to `sym/px/act/rr/v_rules` only.
- `src/futures_copilot/dashboard/app.py:762-766` — the dashboard offers only "Download JSON" and "Download Markdown"; there is no way to obtain the clean prompt by itself.

**Worst-Case Failure Scenario:**
1. Trader clicks "Download Markdown", pastes the whole `.md` into Claude (it is the only pasteable text artifact offered).
2. Claude receives, in one context: `- VWAP 23310.5 (above) · ATR5 14.2 · day range position 0.68` (writer.py:247-248) followed 40 lines later by *"Never mention ATR, VWAP, or any metric absent from the packet"* and *"cite only sym/px/act/rr/v_rules"*.
3. The instruction hierarchy is now self-contradictory. Three failure modes, all observed with instruction-conflicted LLMs: (a) the model cites the ATR/VWAP numbers anyway, then "corrects" itself mid-answer, producing contradictory prose the trader half-reads before a live decision; (b) the model treats the guardrail as applying only to the minified block and free-associates on levels in the human sections — including levels from *Similar past setups* of a DIFFERENT trading day, presented as current; (c) the model, told numbers exist that it may not cite, paraphrases them imprecisely ("volatility looks elevated") — an invented, untraceable claim, exactly what the guardrail exists to prevent.
4. Trader (paper-)trades off commentary contaminated by stale or fabricated levels. The deterministic gate was never wrong — the explanation layer was poisoned by its own packaging.

**Concrete Fix (three coordinated edits):**

**(a) `writer.py` — replace lines 331-340 (the tail of `render_markdown`) so the archival md no longer embeds the prompt:**

```python
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
```

(The literal substring `Respond ONLY with JSON` is retained deliberately — `tests/test_packet.py:67` pins it.)

**(b) `writer.py` — replace `write_packet` (lines 343-354) in full; it now also writes the paste-ready prompt file:**

```python
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
```

**(c) `dashboard/app.py` — the "Download Minified Prompt Only" surface.** Two edits. First, replace the import block at lines 147-149:

```python
    from futures_copilot.packet import (
        export_packet_json, packet_content_hash, packet_from_latest,
        render_claude_prompt, render_markdown, write_packet,
    )
```

(`render_claude_prompt` is already exported by `packet/__init__.py`; `packet_content_hash` is added in Domain 3 — apply Domains 1 and 3 to `app.py` together, they touch the same tab.)

Second, replace the `packet_tab` fragment (lines 747-774) in full:

```python
            @st.fragment
            def packet_tab() -> None:
                with st.container(border=True):
                    st.markdown('<div class="microlabel">Claude prompt packet</div>',
                                unsafe_allow_html=True)
                    st.caption("Claude explains, grades, warns, journals. The decision field does "
                               "not exist in its output schema — the gate already decided.")
                    if pc["error"]:
                        st.info(pc["error"])
                        return
                    st.markdown('<div class="microlabel">Paste THIS into Claude — template + '
                                'minified context, nothing else</div>', unsafe_allow_html=True)
                    st.code(pc["prompt"], language="markdown")
                    pcol1, pcol2, pcol3 = st.columns([1, 1, 1])
                    with pcol1:
                        st.download_button("Download Claude prompt only", pc["prompt"],
                                           file_name="copilot_prompt.txt", mime="text/plain")
                    with pcol2:
                        if st.button("Write packet files (JSON + MD + prompt)"):
                            jp, mp = write_packet(pc["packet"], config)
                            st.success(f"written: {jp.name}, {mp.name}, "
                                       f"{mp.stem}.prompt.txt → {jp.parent}")
                        st.download_button("Download JSON", pc["json"],
                                           file_name="copilot_packet.json", mime="application/json")
                    with pcol3:
                        st.download_button("Download Markdown (archive — do NOT paste)", pc["md"],
                                           file_name="copilot_packet.md", mime="text/markdown")
                    view = st.radio("view", ["markdown", "json"], horizontal=True,
                                    label_visibility="collapsed", key="packet_view")
                    if view == "markdown":
                        st.markdown(pc["md"])
                    else:
                        st.json(pc["packet"], expanded=False)

            packet_tab()
```

(`st.code` ships a built-in copy button — one click grabs the clean prompt. The `pc["prompt"]` cache entry is produced by the Domain 3 replacement below.)

**Accepted residual (documented, not fixed):** `build_packet` at `writer.py:198` still embeds the full template into the JSON packet as `claude_role`. That is the archival/audit copy — `tests/test_packet.py:54` pins `"not yours" in packet["claude_role"]` — and once the prompt-only artifact exists, the JSON is no longer a paste target. Do not "fix" this by deleting `claude_role` unless you also update that test and accept losing the self-describing packet property.

---
## DOMAIN 2 — TEMPORAL BOUNDARY BLIND SPOTS (ROLL DATES)

### Finding 2.1 — The roll gate anchors to the CALENDAR date of the candidate bar; the system's day boundary is 18:00 ET, not midnight

**File:Line:**
- `src/futures_copilot/gate/risk_gate.py:149-151` — `signal_date = datetime.fromtimestamp(cand.ts, tz=ET).date()` then `is_in_roll_window(signal_date)`.
- `src/futures_copilot/features/sessions.py:36-41` — everywhere else, a bar at/after 18:00 ET belongs to the NEXT trading day.

The hypothesized "scan exactly at midnight" hole does not exist as stated: bars between 00:00 and 17:00 ET have calendar date == trading day, and the gate never reads the wall clock (`cand.ts` is anchored, good). The REAL boundary is 18:00 ET, where the two date systems disagree — and the gate uses the wrong one. A second, subtler mismatch: the roll check is made at the candidate's bar time, while every other check is judged at the decision horizon `state.as_of_close_ts` (risk_gate.py:154).

**Worst-Case Failure Scenario:**
1. Roll date Mon 2026-06-15 → window 06-12..06-18. Trading day 06-12 begins Thu 06-11 18:00 ET.
2. Config has `allowed_sessions: ["asia", "london", "ny"]` (the architecture explicitly supports overnight sessions; only the current Desk Mode default is ny-only, config.py:112).
3. An asia-session liquidity trap confirms at 20:05 ET on 06-11. `signal_date = 2026-06-11`; `abs(06-11 − 06-15) = 4 > 3` → `is_in_roll_window` returns **False**. The roll gate passes.
4. The signal persists as `trading_day = "2026-06-12"` — a day INSIDE the roll window. Dashboard header says 06-12; the roll banner (anchored to `state["trading_day"]`, app.py:328-331) screams "ROLL WINDOW ACTIVE — No trades allowed" while the gate's checklist shows every check green and decision LONG. The trader trusts the checklist over the banner ("the gate is the authority, the banner is display-only" — the code comment at app.py:325-327 says exactly that) and takes a paper trade into expiring-contract liquidity, where MNQ1! continuous pricing straddles two contracts and the stop/target geometry computed off pre-roll bars is fiction.
5. Mirror case at the horizon: a candidate confirms 16:55 ET (pre-maintenance, trading day D); the scan evaluates at 18:05 ET (trading day D+1 = window entry). The roll check runs against D and passes even though the DECISION is being made inside the window.

**Concrete Fix — `src/futures_copilot/gate/risk_gate.py`:**

Add one import after line 30 (`from ..utils.roll_dates import is_in_roll_window`):

```python
from ..features.sessions import trading_day as signal_trading_day
```

Replace the head of `_evaluate_candidate` (lines 145-151) with:

```python
def _evaluate_candidate(
    cand: SignalCandidate, state: MarketState, store: Store, config: Config,
    passed_this_run: dict[str, int], counting_from_table: bool,
) -> GateDecision:
    # Roll gate — anchored to TRADING days (the 18:00 ET boundary), not raw
    # calendar dates, and checked at BOTH ends of the decision: the bar that
    # created the candidate AND the horizon the decision is made at. Every
    # date derives from a bar's UTC epoch through the ET-aware trading_day()
    # helper — no wall clock, no local timezone, no midnight/18:00-rollover
    # ambiguity, and no drift between the roll gate and the trading_day label
    # the signal is persisted (and displayed) under.
    cand_day = signal_trading_day(cand.ts, config.sessions)
    horizon_day = signal_trading_day(state.as_of_close_ts, config.sessions)
    if is_in_roll_window(cand_day) or is_in_roll_window(horizon_day):
        return _roll_window_reject(cand)
```

(Everything from `r = config.risk` at line 153 onward is unchanged.) This is atomic by construction: both dates are pure functions of timestamps frozen in the candidate/state snapshot; a date rollover between scan start and gate evaluation cannot change them.

### Finding 2.2 — 2029+: silently unprotected, with a green checkmark

**File:Line:**
- `src/futures_copilot/utils/roll_dates.py:40-42` — `get_next_roll_date` returns `None` for `dt.year > 2028` (and for late-Dec 2028).
- `src/futures_copilot/utils/roll_dates.py:47-48` — `is_in_roll_window` returns `False` for anything past 2028-12-14 + 3d.
- `src/futures_copilot/dashboard/app.py:329-337` — `next_roll = None` → the 48h warning arm is skipped → the `else` branch renders "✅ Normal contract cycle trading."

**Verification performed:** for 2029+ dates, `is_in_roll_window` returns `False` and `get_next_roll_date` returns `None`; neither raises — confirmed by execution. So the requested invariant holds, but it is the WRONG invariant: in March 2029 the trader gets an affirmative green "normal cycle" badge and a fully open gate during an actual CME roll.

**Worst-Case Failure Scenario:** The project runs unattended into 2029 (static calendars outlive their authors — that is their entire failure mode). Roll Mon 2029-03-12 arrives. Gate: open. Banner: green check, actively asserting normalcy. Every liquidity-trap signal that week gates LONG/SHORT off thinning front-month volume; equal-highs/lows detection fires on roll-spread artifacts. Nothing errors, nothing warns — the protection didn't degrade, it VANISHED while claiming to work.

**Concrete Fix — replace `src/futures_copilot/utils/roll_dates.py` in full:**

```python
"""CME equity index contract roll-date helpers.

Hand-verified static calendar for 2026-2028 plus a COMPUTED fallback
(Monday preceding the 3rd Friday of Mar/Jun/Sep/Dec) for later years.
The computed rule reproduces every hand-verified static entry — pinned by
test_computed_rolls_match_static_table — so a trader in 2029+ is never
silently unprotected. `roll_calendar_source()` tells callers (dashboard)
whether the active year is verified or computed, so they can show a
"verify against CME" reminder instead of a silent green check.
"""

from __future__ import annotations

from datetime import date, timedelta

ROLL_WINDOW_DAYS = 3

EQUITY_INDEX_ROLL_DATES: dict[int, tuple[date, ...]] = {
    2026: (
        date(2026, 3, 16),
        date(2026, 6, 15),
        date(2026, 9, 14),
        date(2026, 12, 14),
    ),
    2027: (
        date(2027, 3, 15),
        date(2027, 6, 14),
        date(2027, 9, 13),
        date(2027, 12, 13),
    ),
    2028: (
        date(2028, 3, 13),
        date(2028, 6, 12),
        date(2028, 9, 11),
        date(2028, 12, 11),
    ),
}


def _third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)                     # 3rd Friday is the 15th-21st
    return d + timedelta(days=(4 - d.weekday()) % 7)


def _computed_rolls(year: int) -> tuple[date, ...]:
    """Monday before the 3rd Friday of each contract month (CME convention)."""
    return tuple(_third_friday(year, m) - timedelta(days=4) for m in (3, 6, 9, 12))


def rolls_for_year(year: int) -> tuple[date, ...]:
    """Static (hand-verified) dates when mapped; computed dates beyond the map."""
    return EQUITY_INDEX_ROLL_DATES.get(year, _computed_rolls(year))


def roll_calendar_source(dt: date) -> str:
    """'static' when dt's year is hand-verified, else 'computed'."""
    return "static" if dt.year in EQUITY_INDEX_ROLL_DATES else "computed"


def get_next_roll_date(dt: date) -> date:
    """Next roll date on or after ``dt``. Never None — any year."""
    for year in (dt.year, dt.year + 1):
        for roll in rolls_for_year(year):
            if roll >= dt:
                return roll
    return rolls_for_year(dt.year + 1)[0]         # defensive; unreachable


def is_in_roll_window(dt: date) -> bool:
    """Whether ``dt`` is inside a +/- ROLL_WINDOW_DAYS roll window, any year."""
    for year in (dt.year - 1, dt.year, dt.year + 1):
        for roll in rolls_for_year(year):
            if abs((dt - roll).days) <= ROLL_WINDOW_DAYS:
                return True
    return False
```

Executed verification of this exact code: `_computed_rolls(y) == EQUITY_INDEX_ROLL_DATES[y]` for all of 2026/2027/2028 (12/12 dates); `get_next_roll_date(2028-12-12) == 2029-03-12`; `is_in_roll_window(2029-03-09..2029-03-15) is True`, `2029-03-08` and `2029-01-01` False; all pre-existing 2026-2028 boundary assertions still hold.

**Required test update — `tests/test_roll_dates.py`:** lines 80-81 pin the old `None` behavior. Replace the test at lines 77-81 with:

```python
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
```

### Finding 2.3 — Dashboard banner falls back to the machine's LOCAL date before the first scan

**File:Line:** `src/futures_copilot/dashboard/app.py:328` — `roll_day = date.fromisoformat(state["trading_day"]) if state else date.today()`.

**Worst-Case Failure Scenario:** Fresh DB, no scan yet. Trader in Los Angeles opens the dashboard Sunday 21:30 PT = Monday 00:30 ET; `date.today()` returns Sunday while the ET trading day is already Monday — on a roll Monday the banner under-warns by a day. Any non-ET machine (or a VPS in UTC+) skews the 48h pre-warning the same way. Display-only, but it is the trader's primary pre-scan roll surface.

**Concrete Fix — replace the roll-indicator block, `app.py:324-337`:**

```python
        # ── contract roll indicator ──────────────────────────────────────
        # Static+computed CME calendar (utils/roll_dates.py). Display only —
        # the GATE enforces the same window in code (risk_gate). Anchored to
        # the trading day on screen; before the first scan, falls back to the
        # CURRENT ET TRADING DAY (18:00 ET boundary), never the machine's
        # local calendar date.
        if state:
            roll_day = date.fromisoformat(state["trading_day"])
        else:
            from futures_copilot.features.sessions import trading_day as _tday
            roll_day = _tday(int(time.time()), config.sessions)
        next_roll = get_next_roll_date(roll_day)
        window_opens = next_roll - timedelta(days=ROLL_WINDOW_DAYS)
        if is_in_roll_window(roll_day):
            st.error("🚨 ROLL WINDOW ACTIVE — No trades allowed.")
        elif (window_opens - roll_day) <= timedelta(hours=48):
            st.warning("⚠️ Roll window approaching within 48 hours. "
                       "Monitor contract volume shift.")
        else:
            st.caption("✅ Normal contract cycle trading.")
        if roll_calendar_source(roll_day) == "computed":
            st.caption("Roll calendar is past the hand-verified table — dates are "
                       "computed (Mon before 3rd Friday). Verify against CME and "
                       "extend EQUITY_INDEX_ROLL_DATES.")
```

And extend the import at `app.py:150-152`:

```python
    from futures_copilot.utils.roll_dates import (
        ROLL_WINDOW_DAYS, get_next_roll_date, is_in_roll_window,
        roll_calendar_source,
    )
```

---
## DOMAIN 3 — STATE CACHE INVALIDATION EXPLOITS

### Finding 3.1 — The packet cache key is an enumeration of guesses, and four inputs escape it

**File:Line:** `src/futures_copilot/dashboard/app.py:731-745` (key built at 732-734), fed by `src/futures_copilot/dashboard/data.py:87-88`.

```
pkt_key = symbol : latest_signal_id : state.ts : len(mistakes) : len(reviews) : bias_value : preflight_status
```

Confirmed stale-cache vectors, each one a real code path:

1. **Review edits are invisible.** `store.add_trade_review` is an upsert (`db/store.py:295-328`, `ON CONFLICT(signal_id) DO UPDATE`) — editing a review's `notes`/`result_r` keeps both the row id and the row COUNT identical. `len(ov["reviews"])` doesn't move → packet shows the old journal note and old R-result in `similar_setups`/`preflight` context.
2. **The `len()`s are capped at 12.** `data.py:87-88` loads `limit=12`. The 13th mistake (or 13th review) leaves `len() == 12` forever — from that point, journaling new mistakes NEVER invalidates the packet.
3. **Retro backfill at an unchanged horizon.** "Refresh data" re-runs backfill + scan; a corrected volume on already-stored candles rewrites VWAP into a NEW `market_states` row — but with the SAME `state["ts"]` if no new bar closed (`store.py:151-159` picks by `ts DESC, id DESC`). Key unchanged → the packet's `market_state` (and its VWAP/ATR) is the pre-correction one.
4. **Bias notes and session-prep are not in the key at all.** The key holds `bias["bias"]` only — editing the thesis text with the same direction is invisible; `packet["daily_bias"]["notes"]` goes stale. Running `copilot prep` mid-session (new vault cache → new `session_prep` packet section) changes nothing in the key either.

**Worst-Case Failure Scenario:** Trader journals "moved_stop — NEVER move stops on trap longs" as an edit to an existing review (vector 1), rebuilds preflight, then opens the Packet tab and pastes the prompt. The packet — cache-hit from before the edit — carries the old journal state; Claude's `mistake_echoes` misses the exact mistake the trader just promised themselves not to repeat, minutes before the next trap long sets up. The memory system's one job fails silently at its highest-leverage moment.

**Concrete Fix — content-addressed key.** Stop enumerating invalidation triggers; hash the packet itself. The packet build is a handful of indexed SQLite reads plus two small JSON files (all local, milliseconds) — rebuild it every rerun and use a SHA-256 of its payload to decide whether the RENDERED artifacts (md/json/prompt) can be reused.

**(a) `src/futures_copilot/packet/writer.py` — add `hashlib` to the imports (after line 25's `import json`):**

```python
import hashlib
import json
```

**(b) `writer.py` — add below `export_packet_json` (after line 143):**

```python
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
```

**(c) `writer.py` — stamp it into every packet: in `build_packet`, directly after line 211 (`packet["claude_packet_min"] = minify_packet(packet)`):**

```python
    # Content-addressed identity: consumers (dashboard cache, Obsidian
    # ingestion, file dedupe) use this to detect REAL changes.
    packet["content_hash"] = packet_content_hash(packet)
```

**(d) `src/futures_copilot/packet/__init__.py` — replace in full (adds the export):**

```python
"""Claude prompt-packet layer. Claude explains — it never decides."""

from .writer import (
    CLAUDE_OUTPUT_SCHEMA,
    build_packet,
    export_packet_json,
    load_template,
    minify_packet,
    packet_content_hash,
    packet_from_latest,
    render_claude_prompt,
    render_markdown,
    write_packet,
)

__all__ = [
    "CLAUDE_OUTPUT_SCHEMA",
    "build_packet",
    "export_packet_json",
    "load_template",
    "minify_packet",
    "packet_content_hash",
    "packet_from_latest",
    "render_claude_prompt",
    "render_markdown",
    "write_packet",
]
```

**(e) `dashboard/app.py` — replace lines 724-745 (the `# ── packet` section comment, `with tabs[5]:`, and the whole cache block down to just above the `@st.fragment` line) with:**

```python
        # ── packet ───────────────────────────────────────────────────────────
        with tabs[5]:
            # Deterministic, content-addressed cache. The packet is rebuilt on
            # every full rerun (a handful of indexed local SQLite reads + two
            # small JSON files — milliseconds); the SHA-256 of its payload
            # decides whether the rendered md/json/prompt artifacts are reused.
            # generated_at is excluded from the hash, so identical inputs hit
            # the cache; ANY real input change misses it. See
            # packet_content_hash() in packet/writer.py for the full rationale.
            try:
                packet = packet_from_latest(store, config, symbol)
                pkt_key = f"{symbol}:{packet_content_hash(packet)}"
                pc = st.session_state.get("_packet_cache") or {}
                if pc.get("key") != pkt_key:
                    pc = {"key": pkt_key, "packet": packet,
                          "md": render_markdown(packet),
                          "json": export_packet_json(packet),
                          "prompt": render_claude_prompt(packet),
                          "error": None}
                    st.session_state["_packet_cache"] = pc
            except ValueError as e:
                pc = {"key": f"{symbol}:error", "packet": None, "md": "",
                      "json": "", "prompt": "", "error": str(e)}
                st.session_state["_packet_cache"] = pc
```

(The old `sig_row = ov["latest_signal"]` line existed only to feed the guess-key — delete it. The `packet_tab` fragment that follows is the Domain 1(c) replacement.)

---

## DOMAIN 4 — ABSOLUTE PRICE LEVEL PRECISION LOSS

### Finding 4.1 — One rounding constant flattens prices AND 0-1 fractions

**File:Line:** `src/futures_copilot/packet/writer.py:46` (`_EXPORT_FLOAT_DECIMALS = 2`), applied blanket-recursively by `_round_floats` (lines 92-102) to the whole packet in `export_packet_json` (lines 141-143).

For MNQ/MES prices this is exactly right (0.25 ticks are binary-exact at 2 dp). For `day_range_position` — a 0-1 fraction — it is a 100x coarser grid than the field's meaning: `0.125` (clean eighth of the day range) exports as `0.12` (`round()` is banker's — verified by execution), `0.0049` exports as `0.0`, and the premium/discount midline test `0.4999 vs 0.5001` collapses to `0.5 vs 0.5`.

**Worst-Case Failure Scenario:** Trader reviews the exported JSON packet (or a future consumer — the Obsidian flash matrix below — ingests it). `day_range_position: 0.5` prints for an actual 0.5049: structurally "at the midline, no edge" when Python's gate math saw discount territory. Claude, told to trace claims to packet numbers, correctly reports the WRONG regime ("price sits dead at the equilibrium of the day's range") — a fabrication laundered through rounding, invisible because it looks precise.

**Concrete Fix — field-aware rounding. Two edits to `writer.py`. First, replace the constants at lines 45-48 with:**

```python
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
```

**Second, replace `_round2` + `_round_floats` (lines 84-102) with the block below.** (`CLAUDE_OUTPUT_SCHEMA` and `load_template`, which sit between the two regions at lines 50-81, are NOT touched. String fields like `vwap_position` pass through — the walker only rounds floats.)

```python
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
```

The minified block is deliberately NOT widened: `px` at 2 dp matches the template's documented contract (`fable_review.md:36`), `rr` at 2 dp is ample against `min_rr` thresholds, and `day_range_position` does not travel in the minified context. Token cost: unchanged. No test pins exported `day_range_position` values (verified by grep), so this is drop-in.

---
## DOMAIN 5 — RESTORE PATHS & DISPATCH LOCKUPS

### Finding 5.1 — The hypothesized SQLite stale-lock failure does NOT exist; here is the proof

**Audited paths:** every `Store` open in the codebase is exception-safe already:
- `dashboard/app.py:279` + `775-776` — `store = D.open_store(config)` immediately followed by `try: … finally: store.close()`.
- `dashboard/app.py:29-36, 47-56` — `_refresh_symbol`/`_collect_symbol` use `with Store(...)` nested in `try/finally source.close()`.
- `cli.py:55, 76, 90, 109, 118, 130, 176, 219, 242, 303` — every command uses `with Store(...)`; `cmd_collect` (cli.py:85-103) catches `KeyboardInterrupt` OUTSIDE the `with`, so Ctrl+C during `collect_loop` unwinds the context manager and closes cleanly.
- Every write method in `db/store.py` commits per-call; an exception mid-write leaves an implicit open transaction that `conn.close()` rolls back.

**Crash simulation executed (SIGKILL, the worst case — no `finally` runs at all):** a child process opened a WAL-mode DB, began a transaction, inserted, and hard-killed itself (`os.kill(getpid(), SIGKILL)`). Result: `.db-wal` and `.db-shm` DID persist on disk — and were harmless. The next `sqlite3.connect` on the same file read instantly (uncommitted row correctly absent), wrote successfully, and removed both sidecar files on clean close. SQLite WAL recovery is automatic; with `busy_timeout = 5000` already set (`store.py:26`), even a still-running writer only delays, never deadlocks, a reader. **A leftover `-wal`/`-shm` file does not "halt all subsequent reads/writes" — do not add lock-file deletion code; deleting a live `-wal` is the one way to actually corrupt the DB.**

### Finding 5.2 — The REAL dispatch lockup: the MCP bridge leaks an event loop, a thread, and a NODE CHILD PROCESS on every close and every failed connect

**File:Line:** `src/futures_copilot/data/tvmcp.py:82-131`.

Three compounding defects:
1. `_connect` (lines 106-119) enters `stdio_client(...)`/`ClientSession(...)` — anyio-based async context managers — inside ONE asyncio task; `close()` (lines 121-131) calls `self._stack.aclose()` from a DIFFERENT task via `run_coroutine_threadsafe`. anyio cancel scopes must be exited by the task that entered them; the cross-task `aclose()` raises `RuntimeError` inside the loop, which lines 125-126's `except Exception: pass` swallows. The spawned **node child process survives**. This happens on the SUCCESS path — every single `source.close()`.
2. On a connect timeout (`fut.result(timeout=…)`, line 96), `_ensure_connected` raises but leaves `self._loop`/`self._thread` alive and `self._session = None`; the next call sees `_session is None` and builds a SECOND loop + thread while the first still runs. Nothing is ever reaped.
3. `close()` never joins `self._thread` and never closes the loop object.

**Worst-Case Failure Scenario (step-by-step, live mode):**
1. Trader enables Live mode: quote sync every 1s, data scan every 30s (app.py:200-254). Each `_quote_symbol`/`_collect_symbol` constructs a fresh `TradingViewMcpCandleSource` and calls `close()` in `finally`.
2. Every cycle orphans one node bridge process (defect 1): ~120 orphans/hour at a 30s cadence.
3. TradingView Desktop's CDP endpoint (`:9222`) accumulates zombie websocket clients; memory climbs; eventually new CDP connections are refused or time out.
4. Now the FAILURE path amplifies it: each 30s tick times out (defect 2), stacking an extra event loop + daemon thread per tick on top of the orphaned children.
5. Scans stop mid-session with `CdpUnreachable`; "Refresh data" fails too (same leak); the dashboard shows stale state with a live-looking chart. The lockup is NOT in SQLite — it is a resource-exhaustion spiral between the adapter and TradingView, and it persists until the trader manually kills every orphaned `node.exe` or reboots. During a session, that is the difference between standing down cleanly and trading blind.

**Concrete Fix — single-task lifecycle. Replace `tvmcp.py` `__init__` (lines 72-79) and the entire connection-management section (lines 81-131: `_ensure_connected`, `_connect`, `close`) with the following; `_connect` is DELETED:**

```python
    def __init__(self, config: Config):
        self.config = config
        self.tv = config.data.tvmcp
        self.server_path = config.resolve(self.tv.server_path)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session = None
        self._ready: threading.Event | None = None
        self._shutdown: asyncio.Event | None = None
        self._lifecycle_fut = None
        self._lifecycle_error: BaseException | None = None

    # ── connection management (background loop, single-task lifecycle) ───────
    def _ensure_connected(self) -> None:
        if self._session is not None:
            return
        if self._loop is not None:
            # A previous attempt failed half-open. Tear it down BEFORE retrying,
            # or every retry leaks a loop + thread (+ a node child process).
            self.close()
        if not self.server_path.exists():
            raise BridgeNotFound(
                f"MCP bridge not found at {self.server_path}",
                hint="clone tradesdontlie/tradingview-mcp into vendor/ and run `npm install` there "
                     "(see README: Setup step 2)",
            )
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._ready = threading.Event()
        self._lifecycle_error = None
        self._lifecycle_fut = asyncio.run_coroutine_threadsafe(self._lifecycle(), self._loop)
        ok = self._ready.wait(timeout=self.tv.request_timeout_s)
        if not ok or self._session is None:
            err = self._lifecycle_error
            self.close()   # cancels the lifecycle task -> stack unwinds IN-TASK -> node dies
            if isinstance(err, DataSourceError):
                raise err
            raise CdpUnreachable(
                f"could not start/connect MCP bridge: {err or 'timed out'}",
                hint="is Node installed? is TradingView Desktop running with "
                     "--remote-debugging-port=9222? (use vendor/tradingview-mcp/scripts/launch_tv_debug.bat)",
            ) from err

    async def _lifecycle(self) -> None:
        """Own the MCP transport for its WHOLE life inside ONE asyncio task.

        mcp's stdio_client is anyio-based: its cancel scopes must be exited by
        the same task that entered them. The previous design entered the stack
        in one task (_connect) and aclose()d it from another (close), which
        raises RuntimeError inside anyio — silently swallowed — and leaked the
        spawned node child process on every close and every failed connect.
        Here, enter and exit both happen in THIS task; close() merely signals.
        """
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._shutdown = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                params = StdioServerParameters(
                    command=self.tv.node_command,
                    args=[str(self.server_path)],
                )
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self._session = session
                self._ready.set()
                await self._shutdown.wait()      # hold the transport open until close()
        except BaseException as e:               # includes CancelledError from close()
            self._lifecycle_error = e
            if self._ready is not None:
                self._ready.set()
            raise
        finally:
            self._session = None                 # stack has unwound in-task: node is dead

    def close(self) -> None:
        if self._loop is None:
            return
        try:
            if self._shutdown is not None:
                self._loop.call_soon_threadsafe(self._shutdown.set)
            if self._lifecycle_fut is not None:
                try:
                    self._lifecycle_fut.result(timeout=10)    # same-task cleanup completes
                except BaseException:
                    # Hung mid-connect (e.g. CDP half-open): cancellation also
                    # unwinds the stack IN-TASK, so the child is still reaped.
                    self._lifecycle_fut.cancel()
                    try:
                        self._lifecycle_fut.result(timeout=5)
                    except BaseException:
                        pass
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=5)
            try:
                self._loop.close()
            except RuntimeError:
                pass
            self._session = None
            self._loop = None
            self._thread = None
            self._ready = None
            self._shutdown = None
            self._lifecycle_fut = None
            self._lifecycle_error = None
```

`_call` (lines 134-173) is unchanged — it still talks to `self._session` on `self._loop`. `self._stack` no longer exists; nothing else referenced it (verified by grep). `tests/test_tvmcp_unit.py` does not touch `_connect`/`_session` internals (verified), so the suite is unaffected.

**Post-fix verification to run on the Windows box (live CDP validation is still pending per project state):** enable Live mode at a 10s scan interval with TradingView CLOSED for 3 minutes, then `tasklist | findstr node` — expect ZERO accumulated node processes; then open TradingView and confirm recovery without restarting Streamlit.

### Finding 5.3 — `Store.__exit__` relies on implicit rollback; make the guard explicit

**File:Line:** `src/futures_copilot/db/store.py:66-70`.

**Worst-Case Failure Scenario:** low severity — `sqlite3.Connection.close()` does roll back an open implicit transaction — but the contract is undocumented in the code and silently load-bearing for every `with Store(...)` block listed above. One future refactor to a pooled/shared connection breaks crash-consistency invisibly.

**Concrete Fix — replace lines 66-70:**

```python
    def __enter__(self) -> "Store":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is not None:
                # A write blew up mid-transaction: drop the half-done tx
                # EXPLICITLY instead of relying on close()'s implicit rollback.
                self.conn.rollback()
        finally:
            self.close()
```

---
## DOMAIN 6 — OBSIDIAN "2ND AI BRAIN" INTEGRATION & CONTEXT PURGE

### Finding 6.1 — The vault has an allowlist but no lifecycle: nothing ever stops being true

**File:Line:**
- `src/futures_copilot/prep.py:27-33` — `VAULT_ALLOWLIST` is fixed (good), but `build_session_prep` (lines 57-70) ships whatever those five notes contain, verbatim, into `session_prep` → the packet → Claude. There is no status, no expiry, no supersession — a deprecated rule inside an allowlisted note is indistinguishable from an active one.
- `06_OBSIDIAN_VAULT_PLAN.md` — defines folders and seed notes; defines NO front matter, NO deprecation, NO purge protocol.

**Worst-Case Failure Scenario:**
1. July: trader writes "Risk Management.md → experimental: allow London session traps while testing" and runs `copilot prep` daily.
2. September: London experiment fails; the trader flips config back to ny-only but only APPENDS a journal note — the old paragraph in the allowlisted note stays. (Nothing marks it dead; nothing sweeps it.)
3. October, 03:40 ET: a London sweep sets up. The packet's `session_prep` carries the July paragraph. Claude — told the vault is context — blends it in: "your notes indicate London traps are within plan." The gate correctly REJECTs on `session_allowed`, but the trader, reading Claude's vault-backed encouragement next to a red checklist, overrides manually (paper, then not-paper habits form). Contradictory long-term memory beats the deterministic gate through the HUMAN channel — the one channel the safety design cannot gate.
4. The same rot compounds inside the Obsidian AI plugin: every stale session analysis stays indexed forever, and semantic retrieval has no recency/status signal to prefer the current rule over the dead one.

**Concrete Fix — three artifacts:**

**(a) Mandatory front-matter schema for EVERY vault note the copilot or the Obsidian AI may read** (add to `06_OBSIDIAN_VAULT_PLAN.md` and to each seed template):

```yaml
---
type: core_rule        # core_rule | mistake_rule | strategy_module | daily_bias
                       # | trade_review | session_log | flash_summary | scratch
status: active         # active | deprecated | superseded
supersedes: ""         # [[wikilink]] to the note this replaces (if any)
superseded_by: ""      # [[wikilink]] filled in when THIS note dies
symbol: MNQ            # MNQ | MES | all
created: 2026-07-08
review_after: 2026-10-08   # human re-validation date (rules go stale silently)
expires: ""            # hard AI-ingestion cutoff, ISO date; "" = never
authority: none        # ALWAYS none — vault text NEVER decides; gate + human do
content_hash: ""       # packet content_hash when a note is copilot-generated
---
```

**Ingestion rule (enforce in the Obsidian AI's system prompt AND in prep.py below):** a note may reach any AI context only if `status: active` AND (`expires` empty OR `expires >= today`). `session_log`/`trade_review` notes are diary, not doctrine: ingested only when a query explicitly asks for history. Deprecation protocol: never delete — set `status: deprecated`, fill `superseded_by`, move to `99 Archive/` after 30 days. One rule of truth per topic: creating a replacement note REQUIRES setting `supersedes` on the new and `superseded_by` on the old in the same edit.

**(b) Code guard so `copilot prep` physically cannot ship dead notes — `src/futures_copilot/prep.py`. Add after `prep_cache_path` (line 38):**

```python
def _front_matter(text: str) -> dict[str, str]:
    """Cheap front-matter scalars (status/expires/...). No YAML dependency."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    for line in text[3:end].splitlines():
        key, sep, val = line.partition(":")
        if sep:
            out[key.strip().lower()] = val.strip().strip('"').strip("'")
    return out


def _note_is_ingestible(text: str, today: str) -> tuple[bool, str]:
    """(ok, reason). Deprecated/superseded/expired notes must NOT reach Claude.
    Notes without front matter default to active — additive, non-breaking."""
    fm = _front_matter(text)
    status = (fm.get("status") or "active").lower()
    if status != "active":
        return False, f"status:{status}"
    expires = fm.get("expires") or ""
    if expires and expires < today:            # ISO dates compare lexically
        return False, f"expired:{expires}"
    return True, ""
```

**And replace the read loop inside `build_session_prep` (lines 54-70) with:**

```python
    max_chars = int(config.vault.max_chars_per_note)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    notes: list[dict[str, Any]] = []
    missing: list[str] = []
    excluded: list[dict[str, str]] = []
    for rel in VAULT_ALLOWLIST:
        p = root / rel
        if not p.exists():
            missing.append(rel)
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        ok, why = _note_is_ingestible(text, today)
        if not ok:
            excluded.append({"path": rel, "reason": why})
            continue
        truncated = len(text) > max_chars
        notes.append({
            "path": rel,
            "title": p.stem,
            "chars": len(text),
            "truncated": truncated,
            "content": text[:max_chars],
        })
```

**And add `"excluded": excluded,` to the returned dict (after `"missing": missing,` at line 78)** — the packet then SHOWS what was withheld and why, so a mis-tagged note is visible instead of silently absent.

**(c) Flash Summary Matrix — the compression protocol.** One note per symbol, regenerated after each session, `status: active` on exactly one at a time (previous one flips to `deprecated`). Pure numbers and boundaries; zero prose for the Obsidian AI to over-interpret. Exact template:

```markdown
---
type: flash_summary
status: active
symbol: MNQ
created: 2026-07-08
expires: 2026-07-15
sessions_covered: 5
authority: none
content_hash: ""
---
# Flash Summary Matrix — MNQ — 5 sessions to 2026-07-08

| day   | sess | act    | setup | dir | gr | rr   | res_R | mist | PDH      | PDL      | ONH      | ONL      | drp    |
|-------|------|--------|-------|-----|----|------|-------|------|----------|----------|----------|----------|--------|
| 07-08 | ny   | LONG   | trap  | L   | A  | 2.40 | +1.80 | 0    | 23412.25 | 23288.50 | 23390.00 | 23301.75 | 0.6812 |
| 07-07 | ny   | REJECT | trap  | S   | B  | 1.60 |       | 1    | 23388.00 | 23244.75 | 23361.50 | 23260.25 | 0.4410 |
| 07-06 | ny   | WAIT   |       |     |    |      |       | 0    | 23301.25 | 23178.00 | 23295.75 | 23190.50 | 0.5220 |
| 07-03 | ny   | LONG   | trap  | L   | B  | 2.10 | -1.00 | 2    | 23255.50 | 23101.25 | 23212.00 | 23119.75 | 0.3106 |
| 07-02 | ny   | LONG   | trap  | L   | A  | 2.40 | +1.80 | 0    | 23198.75 | 23044.50 | 23155.25 | 23061.00 | 0.6255 |

RULES_ACTIVE: min_rr=2.0; expiry=6x5m; sessions=ny; golden=09:30-11:00; stop_on_first_win=true; max_losses=2
BOUNDARIES: roll_window=2026-09-11..2026-09-17; news_blackouts=<from config.yaml>
MISTAKE_TAGS_OPEN: moved_stop x3; chased_entry x2
```

Column key (documented once in the vault plan, not per note): `act` = final gate decision; `gr` = grade; `res_R` = journaled result (blank = not taken); `mist` = mistake tags logged that day; `drp` = day_range_position at decision (4 dp — Domain 4's precision fix is what makes this column honest). Every number is copyable straight from the packet JSON and the Journal tab. The `expires: +7d` guarantees an abandoned vault stops feeding the AI within a week — stale matrices self-purge from ingestion without any cron.

Populating it stays HUMAN/deterministic (copy from packet + journal). If it is ever automated, it belongs in a `copilot flash` command that reads SQLite — never an LLM writing its own memory.

---

## APPLY ORDER & TEST DELTA

1. `utils/roll_dates.py` — full file replacement (2.2). Then `tests/test_roll_dates.py` lines 77-81 → replacement tests (2.2).
2. `gate/risk_gate.py` — import + `_evaluate_candidate` head (2.1).
3. `packet/writer.py` — hashlib import (3b), constants/rounders block (4.1), `render_markdown` tail (1a), `write_packet` (1b), `build_packet` stamp (3c), `packet_content_hash` (3b).
4. `packet/__init__.py` — full replacement (3d).
5. `db/store.py` — `__exit__` (5.3).
6. `data/tvmcp.py` — `__init__` + connection section (5.2).
7. `dashboard/app.py` — imports (1c, 2.3), roll banner (2.3), packet cache block (3e), `packet_tab` (1c).
8. `prep.py` — front-matter guard (6b).
9. Run `pytest -q` — expected: 199 pass with the two test edits from step 1; new tests from 2.2 bring the count to 201+.

Expected test-suite touchpoints, verified against the current suite: `test_roll_dates.py:80-81` (pinned `None` — replaced), everything else additive. `test_packet.py:54/65/66/67` substrings are all preserved by design.

## RESIDUAL RISKS THIS AUDIT DID NOT FIX (KNOW THEM)

1. `minify_packet` (`writer.py:124`): a missing `current_price` exports as `px: 0.0` — the template documents `rr: 0.0` = "no candidate" but has no such convention for `px`. Today `market_state` always carries a price; if that ever changes, prefer failing the packet build over exporting a zero price.
2. `claude_role` still embeds the full template in the JSON packet (Domain 1 residual, intentional).
3. `load_template()` caches forever per process (`writer.py:43,75-81`): editing `fable_review.md` requires restarting Streamlit/CLI. Cheap to fix with an mtime check; left out to keep this diff surface minimal.
4. The computed roll calendar encodes the CME CONVENTION, not CME's published calendar. Exchange holidays could shift an actual roll by a day. The dashboard's "computed — verify against CME" caption plus the ±3-day window absorb this, but extend the static table each year regardless.
5. The 199-test suite was NOT executed in this audit environment (Windows venv, no Linux runner); every fix was line-verified against the pinned assertions and the roll-date module was executed standalone. Run the suite before commit — v0.2/v0.3 are still uncommitted in git, so commit a known-green baseline FIRST, then apply this audit as its own commit.

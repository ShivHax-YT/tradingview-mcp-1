# Desk Mode (v0.2)

Desk Mode turns the v0.1 copilot into a stricter NY-morning desk assistant.
Same architecture, tighter discipline:

> **Python calculates. The risk gate decides. Claude/Fable explains later.
> The human manually approves. WAIT is the default.**

Nothing in Desk Mode places, sizes, or alerts anything. Manual paper trading
only, permanently.

## Golden-hour rule

`risk.enforce_golden_hour` (default `true`) rejects actionable candidates
outside `risk.golden_hour` (default `["09:30", "11:00"]`, America/New_York).

- The decision clock is the market state's `as_of_close_ts` (the knowledge
  horizon) — the same closed-candle clock every other check uses. No lookahead.
- **09:30 is inclusive; 11:00 is exclusive.** A horizon of exactly 09:30 passes,
  exactly 11:00 rejects.
- Checklist item: `golden_hour_allowed`.
- `risk.allowed_sessions` also defaults to `["ny"]` in Desk Mode.

Why: both research anchors in the vault (PB Blake's four-step model, Desi
Trades) converge on the 09:30–11:00 window as the highest-quality delivery
window. Whether it actually outperforms is an open question the journal is
supposed to answer — the gate just enforces the discipline while we collect
those 50–100 samples.

## Trade governor rule

The governor reads **journaled results** (`trade_reviews` joined to the
signals of the same symbol + trading day) and ends the day early:

- `risk.stop_on_first_win: true` — any taken trade with `result_r > 0` blocks
  further candidates. One win = done.
- `risk.stop_after_losses: 2` — two taken trades with `result_r < 0` block
  further candidates. Two losses = done.
- Skipped trades (`taken = false`) and scratch/zero/unresolved results count
  as **neither** win nor loss.
- Checklist item: `trade_governor_clear`.

The governor only knows what you journal. Journal honestly, immediately —
that is the whole point of the copilot.

## Equal-level stop magnet rule

Stops parked inside obvious equal highs/lows are resting liquidity — exactly
what engineered sweeps run before the real move.

- Long candidates: recent **lows** near the proposed stop are inspected.
  Short candidates: recent **highs**.
- Tolerance = `risk.equal_level_tolerance_ticks` (default 3) × the symbol's
  `tick_size`. Lookback = `risk.equal_level_lookback_bars` (default 50) bars
  on the candidate's detection timeframe.
- Only bars **closed at or before the market-state horizon** are considered
  (no lookahead), and **at least 2 matching bars** are required to reject.
- Checklist item: `stop_not_at_equal_liquidity`.
- Toggle: `risk.reject_equal_level_stop_magnets`.

## Why SMT / IFVG are evidence only in v0.2

The vault's open questions explicitly ask whether SMT should be a hard gate —
that is unanswered, so it is **not** a gate. A real SMT engine needs reliably
synchronized MNQ/MES candles, which is itself an open data question.

IFVG inversion rides the existing FVG lifecycle (`inverted_ts` = body close
through the gap): when an opposite-kind gap inverts after the sweep, the
liquidity-trap candidate gains an `ifvg_inversion` confluence tag and context
entry. It can improve a grade; it cannot create a candidate, pass a check, or
override a rejection. A dedicated IFVG strategy module (PB Blake's four-step
model) stays in the backlog until the journal earns it.

## Session prep cache (Obsidian, off the hot path)

`copilot prep --symbol MNQ --day YYYY-MM-DD` reads a **fixed allowlist** of
five vault notes (command center, copilot plan, liquidity-trap strategy, risk
management, open questions), truncates each to `vault.max_chars_per_note`,
and writes `data/session_prep/MNQ_YYYY-MM-DD.json`.

- The packet and dashboard read that cache **if present**.
- The live loop never touches the vault: no crawling, no per-tick reads.
- `copilot scan`, the risk gate, and the dashboard all work with no vault at
  all.

## Live latency principle: Python/risk gate first, Claude/Obsidian later

The dashboard's LONG/SHORT/WAIT/REJECT comes exclusively from the
closed-candle Python scan plus the risk gate — cheap, local, deterministic.
Claude/Fable packets and Obsidian context are prepared to the side (packet
files, prep cache) and consumed after the fact. The screen never blocks on a
model call or a vault read; the live quote sync is display-only and reported
separately from the closed-candle scan status.

## Checklist additions (audit trail)

| Check | Rejects when |
|---|---|
| `golden_hour_allowed` | horizon outside [09:30, 11:00) ET and enforcement on |
| `trade_governor_clear` | a journaled win (stop-on-first-win) or ≥ N journaled losses today |
| `stop_not_at_equal_liquidity` | ≥ 2 recent closed bars with lows/highs within tolerance of the stop |

Everything else from v0.1 — min RR, staleness, chase, ATR caps, session/chop
filters, manual news blackouts, per-session/day caps, gate as sole decision
writer, Claude output schema with no decision field — is unchanged.

## See also

Desk Memory / Preflight (v0.3) — pre-session journal-memory cache and the
dashboard Desk Reminders panel: `docs/DESK_MEMORY.md`.

# Desk Memory / Preflight (v0.3)

Preflight turns your own journal into pre-session reminders. Before the
session, `copilot preflight` reads recent SQLite journal data (trade reviews
joined to their signals, plus the mistake ledger) and writes a compact JSON
cache under `data/session_memory/`. The dashboard's **Desk Reminders** panel
and the Claude/Fable packet read that cache so that repeated mistakes, recent
setup performance, and do-not-repeat rules are in front of you **before any
trade is taken**.

## What it is

Deterministic journal memory: counting, grouping, and plain-language
reminders. The same rows produce the same cache every time. Everything in it
can be traced back to a specific review or mistake entry you wrote.

## What it is not

- **Not model training.** No fitting, no embeddings, no black box, no
  network. It is statistics over your journal, nothing more.
- **Not a decision-maker.** The cache carries zero authority. The risk gate
  remains the sole writer of LONG/SHORT/WAIT/REJECT; you remain the only one
  who approves a paper trade. Claude/Fable's output schema still has no
  decision/action/entry/stop/target/size/execute/order/approve fields.
- **Not an Obsidian reader.** The vault belongs to `copilot prep` only.
  Preflight touches SQLite and one local JSON file.

## Running it (before every session)

```bat
.venv\Scripts\copilot prep      --symbol MNQ    :: optional vault context cache
.venv\Scripts\copilot preflight --symbol MNQ    :: journal memory cache
.venv\Scripts\copilot dashboard                 :: trade the plan, journal honestly
```

`--day YYYY-MM-DD` overrides the trading day (default: today's, using the
18:00-ET trading-day logic). Preflight needs no TradingView, no bridge, no
network — it works on a dead market.

## What the cache contains

`data/session_memory/MNQ_YYYY-MM-DD.json`: schema version, generation time,
source counts, a trade-governor snapshot, top mistake tags (counted from
review `mistake_tags`), do-not-repeat rules (from the mistake ledger),
a performance summary (reviewed/taken/skipped/wins/losses/scratches/average
R on taken-with-result), setup performance grouped by setup + direction +
session (only groups with at least `preflight.min_samples_for_pattern`
samples — small groups are noise, not patterns), plus ready-made warning
bullets for the dashboard and packet bullets for Claude/Fable.

Lookbacks are conservative and configurable under `preflight:` in
`config.yaml` (`lookback_reviews: 50`, `lookback_days: 20`, `top_mistakes: 5`).

## How it stays off the live decision path

The live loop is unchanged: TradingView → Python features → strategy → risk
gate → dashboard decision. Preflight summarization runs only in the CLI
command or the dashboard's explicit "Refresh preflight memory" button (both
local-only). Per dashboard refresh, the panel does one JSON read plus two
MAX(id) staleness lookups; the live scan/gate code path never imports the
preflight module at all (there is a test for that). When you journal a
review, result, or mistake, the panel flips to **stale — journal changed**;
refresh whenever convenient.

## Why journaling matters

Every reminder is derived from what you journal. Skips and scratches count
as neither wins nor losses (same convention as the trade governor); mistake
tags become counted patterns once they repeat; ledger rule updates surface
as do-not-repeat lines. Journal honestly and immediately, and the preflight
gets sharper every day — that feedback loop is the entire point of the
copilot.

# Progress

## Done (Desk Memory / Preflight v0.3, 2026-07-05)
- **Preflight module** (`preflight.py`): deterministic journal memory — recent
  reviews joined to signals (`Store.reviews_with_signals`) + mistake ledger →
  compact JSON cache `data/session_memory/SYMBOL_DAY.json` (performance
  summary, top mistake tags, do-not-repeat rules, setup groups gated by
  `min_samples_for_pattern`, governor snapshot, warning/packet bullets).
  Statistics and reminders, NOT model training; zero decision authority.
- **CLI**: `copilot preflight --symbol MNQ [--day D]` — SQLite only, works
  without TradingView. Config section `preflight:` (lookbacks, caps).
- **Dashboard**: Desk Reminders panel above the Desk Mode strip — cache
  status (cached/stale/missing), top mistakes, performance + governor-at-build,
  warning bullets, local-only "Refresh preflight memory" button. Staleness =
  two MAX(id) lookups per refresh; journaling flips the panel to stale.
- **Packet**: `preflight_memory` embedded when present + "Desk Memory /
  Preflight" markdown section; Claude/Fable output schema still decision-free.
- **Boundary test**: live scan/gate path proven (subprocess test) to never
  import preflight. Docs: `docs/DESK_MEMORY.md`.

## Done (Desk Mode v0.2, 2026-07-05)
- **Risk config**: golden hour (enforce + window), trade governor (stop on first
  win / stop after N losses), equal-level stop-magnet filter (ticks tolerance,
  lookback), `allowed_sessions` default now `["ny"]`; `vault` config for prep.
- **Risk gate**: three new checklist items — `golden_hour_allowed` (09:30 incl /
  11:00 excl ET at the market-state horizon), `trade_governor_clear` (journaled
  wins/losses via `Store.day_trade_results`), `stop_not_at_equal_liquidity`
  (`features/equal_levels.py`, closed bars only, ≥2 matches to reject).
- **IFVG evidence**: liquidity trap tags `ifvg_inversion` confluence when an
  opposite-kind FVG body-closes through after the sweep (existing lifecycle) —
  evidence only, no gate, no new strategy module. SMT stays out of v0.2.
- **Session prep**: `copilot prep --symbol MNQ --day YYYY-MM-DD` caches a fixed
  5-note vault allowlist to `data/session_prep/`; packet embeds it if present;
  scan/gate/dashboard never read the vault (live latency rule).
- **Dashboard**: Desk Mode strip — trade plan (entry/stop/target/invalidation/
  reject reasons), gates (golden hour, governor, equal-level), ops (packet
  readiness, prep cache, closed-candle scan vs live quote sync). Still zero
  execution controls. See `docs/DESK_MODE.md`.

## Done
- **Phase 0/1 — scaffold + data layer** (2026-07-02): config+validators, typed errors,
  SQLite store/schema, tvmcp adapter (read-only allowlist), backfill/collect/resample,
  fixtures, CLI (health/backfill/collect/status/state/init-db/load-fixtures).
  Live gate passed: MNQ bars from TradingView Desktop into SQLite.
- **Phase 2 — feature engine** (2026-07-02): sessions/trading-day math, swings w/
  confirmation lag, sweeps/reclaims, MSS/CISD, FVG lifecycle, VWAP, ATR, pricing,
  MarketState builder with as-of replay.
- **Phase 2 repair** (2026-07-02): levels resolve from finest COVERING timeframe
  (1m→5m→15m→1h) with horizon clipping; `level_sources` reports provenance; OR stays
  1m-only; 4h dropped from canonical until backfill supports it; stale test updated to
  assert the new honest behavior (lone prior-day bar ⇒ None, 15m coverage ⇒ level+source).

## Done (this pass, 2026-07-02)
- **Phase 3 — strategy engine**: plugin interface (`detect(ctx) -> [SignalCandidate]`),
  MTF Session Liquidity Trap complete (sweep → reclaim ≤3 → MSS/CISD ≤12 → FVG/structure
  retest entry → ATR stop beyond extreme → next-liquidity target, graded by confluence),
  ORB breakout/fakeout skeleton (conservative, `orb.enabled: false`).
- **Phase 4 — risk gate**: 11-check pipeline (geometry, confirmed-close, min RR, staleness,
  chase, stop/target ATR caps, session filter, chop filter, manual news blackouts,
  session/day caps with dedupe); LONG/SHORT/WAIT/REJECT; sole writer of `signals.decision`.
- **Phase 5 — Claude packets**: JSON + Markdown writer; packet embeds state, candidate,
  checklist, bias, mistakes, similar setups; Claude output schema is explanation-only
  (`additionalProperties: false`, no decision field). No Anthropic API in v0.1.
- **Phase 6 — journal + vault**: CLI `journal bias|review|result|mistake|show`;
  `trading_brain_seed/` Obsidian vault (templates, playbooks, rules, mistake index, linked graph).
- **Phase 7 — memory**: deterministic SQL similar-setup lookup with auditable bands +
  outcomes; optional LOCAL vector scaffold (in-memory + chroma adapters) off by default.
- **Phase 8 — dashboard**: Streamlit, Legend-style dark theme; overview (candles + level
  ladder + context), signals inspector w/ checklist, journal forms, mistakes, memory,
  packet viewer/downloads. Read + journal only. Headless boot + AppTest execution verified.
- Suite: **142 tests green** on this host after packet freshness regression coverage
  (target py3.12+ per pyproject).

## Known state / blockers
- Live gate NOT re-run this pass: the build sandbox cannot reach TradingView Desktop's
  CDP :9222 on the host machine. `data/copilot.db` shows 0 bytes from the sandbox
  (either empty or an un-checkpointed WAL artifact of the mount). Run on Windows:
  `copilot health && copilot backfill --symbols MNQ && copilot scan --symbol MNQ`.

## Later / explicitly deferred
- 4h canonical timeframe (needs real backfill support)
- Vector memory as default path (local only, still optional)
- Databento/Tradovate/CME adapters (interface docs only — no paid services in v0.1)
- Anthropic API auto-explanations (packets are consumed manually in v0.1)

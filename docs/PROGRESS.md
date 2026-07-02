# Progress

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

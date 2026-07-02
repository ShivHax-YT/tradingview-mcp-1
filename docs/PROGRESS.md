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

## In flight (this pass)
- Phase 3 strategy engine (liquidity trap full + ORB skeleton)
- Phase 4 risk gate → LONG/SHORT/WAIT/REJECT
- Phase 5 Claude prompt packets (JSON+MD, no decision field)
- Phase 6 journal CLI + trading_brain_seed Obsidian vault
- Phase 7 SQL similar-setup memory (+ optional vector scaffold, off by default)
- Phase 8 Streamlit dashboard (dark Legend-style)

## Later / explicitly deferred
- 4h canonical timeframe (needs real backfill support)
- Vector memory as default path (local only, still optional)
- Databento/Tradovate/CME adapters (interface docs only — no paid services in v0.1)
- Anthropic API auto-explanations (packets are consumed manually in v0.1)

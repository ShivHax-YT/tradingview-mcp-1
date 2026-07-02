# Architecture

```
TradingView Desktop (CME data)          you, the human
        │ CDP :9222                          ▲
        ▼                                    │ approves / journals
vendor/tradingview-mcp  (read-only bridge)   │
        ▼                                    │
data/ (tvmcp adapter → collector → resampler)│
        ▼                                    │
SQLite data/copilot.db  (candles, states, signals, journal, memory)
        ▼
features/ (pure, deterministic, as-of aware)
        ▼
strategies/ (plugin detect(ctx) → SignalCandidate)   ← candidates only
        ▼
gate/risk_gate.py  → LONG / SHORT / WAIT / REJECT    ← sole decision authority
        ▼
packet/ (JSON+Markdown prompt packets for Claude — explain/grade/warn only)
        ▼
memory/ (SQL similar-setup lookup; optional local vector adapter)
        ▼
dashboard/ (Streamlit, local, read + journal only)   +   trading_brain_seed/ (Obsidian vault)
```

## Layers
- **data/** `tvmcp.py` (MCP client, typed errors, read-only allowlist), `collector.py`
  (backfill per-TF native ≤500 bars; incremental 1m + derived resample), `resample.py`,
  `fixtures.py` (tests only).
- **db/** `store.py` + `schema.sql`. Bars keyed (symbol, timeframe, ts-open-UTC), `source` column.
- **features/** sessions, levels (multi-TF coverage fallback), swings (confirmed_close_ts),
  sweeps/reclaims, MSS/CISD, FVG lifecycle, VWAP, ATR, pricing. Pure functions; no I/O.
- **features/market_state.py** assembles the as-of-aware MarketState JSON snapshot.
- **strategies/** `base.py` StrategyContext + Strategy ABC; `liquidity_trap.py` (Module 1,
  full); `orb.py` (skeleton, disabled by default); `engine.py` runs enabled strategies.
- **gate/** converts candidates to final decisions with reject reasons; persists to `signals`.
- **packet/** builds the Claude prompt packet; output schema has NO decision field.
- **journal/** CLI + store methods for reviews, outcomes, mistakes, daily bias.
- **memory/** deterministic SQL similar-setup lookup; `vector.py` optional local adapter
  scaffold (disabled by default, no network embeddings).
- **dashboard/** Streamlit app, dark Legend-style theme, read/journal only.

## Key invariants
1. ts = bar open, UTC epoch seconds; sessions computed in America/New_York.
2. No lookahead: every derived fact carries/obeys a knowledge horizon.
3. Levels: finest covering TF wins; uncovered ⇒ None; OR is 1m-only.
4. Native tvmcp bars are never overwritten by resampled bars.
5. `signals.decision` written by the risk gate only.
6. Data source is tvmcp or explicit fixtures — no third path, ever.

# Futures AI Chart Copilot — v0.1 (Phase 1)

Manual paper-trading decision-support copilot for MNQ/MES.

> **Python calculates. Claude explains. Human approves.**
> No broker execution. No live orders. No autonomous trading. No alerts.
> WAIT is the default. This tool never places a trade, anywhere, ever.

## What Phase 1 contains

- SQLite schema (candles, market_states, strategy_outputs, signals, trade_reviews, daily_bias, mistakes)
- `CandleSource` adapter interface
  - **Product path:** `TradingViewMcpCandleSource` — reads your locally running TradingView
    Desktop (your CME data package) through the vendored
    [tradesdontlie/tradingview-mcp](https://github.com/tradesdontlie/tradingview-mcp)
    CDP bridge. Read/navigation tools only, enforced by an allowlist in code.
  - **Test path:** `FixtureCandleSource` — CSV fixtures, tests/offline dev ONLY. Never a fallback.
- 1m → 3m/5m/15m/1h resampler (complete windows only; partial bars are dropped)
- Collector: `backfill` (deep pull, each timeframe natively) and `collect` (incremental 1m + derived)
- CLI + pytest suite (35 tests)

**Phase 2 — feature engine** (complete): sessions/trading-day logic (18:00 ET
boundary, DST-safe), session + prior-day + opening-range levels, fractal swings
with explicit confirmation lag, liquidity sweep/reclaim, MSS + CISD, FVG/iFVG
with full lifecycle (created/mitigated/filled/inverted — all stamped at the
causing bar's CLOSE, so replay can never know things early), trading-day
anchored VWAP, Wilder ATR, premium/discount, R:R + tick/dollar helpers, and a
`MarketState` assembler:

```bat
copilot state --symbol MNQ                 :: structured market-state JSON from stored bars
copilot state --symbol MNQ --as-of 1782854940   :: reconstruct state at any past moment (replay)
```

No strategy, risk, or Claude layers yet — those are Phases 3-5.

## Setup (Windows, one time)

Requirements already on your machine: Python 3.14, Node 18+, Git, TradingView Desktop with CME data.

```bat
cd "C:\Users\ShivHax\Documents\Futures trading Bot Planning + Scripting\futures-ai-chart-copilot"

:: 1. Python environment
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]

:: 2. Bridge dependencies (vendored source, pinned commit — see vendor\PINNED_COMMIT.txt)
cd vendor\tradingview-mcp
npm install
cd ..\..

:: 3. Run the tests
pytest
```

## Daily use

```bat
:: 1. Launch TradingView Desktop with the debug port (close normal TradingView first)
vendor\tradingview-mcp\scripts\launch_tv_debug.bat

:: 2. Verify the pipeline end to end
copilot health

:: 3. First fill / top-up of history (1m + 5m + 15m + 1h, up to 500 bars each)
copilot init-db
copilot backfill

:: 4. While you're at the desk: keep 1m bars flowing (Ctrl+C to stop)
copilot collect --loop

:: 5. Check what you have (bar counts, freshness, gaps)
copilot status
```

Notes:

- `data_get_ohlcv` returns the newest ≤500 bars per timeframe. That is ~8h of 1m,
  ~5 days of 15m, ~3 weeks of 1h. Run `copilot backfill` daily-ish to accumulate
  history in SQLite; gaps (PC off) stay as honest gaps.
- While collecting, don't manually flip the symbol/timeframe on the chart tab —
  the adapter verifies chart state before every read and aborts with
  `SYMBOL_MISMATCH` rather than storing wrong bars.
- Every failure is a typed error (`CDP_UNREACHABLE`, `BRIDGE_NOT_FOUND`,
  `CHART_NOT_READY`, ...) with a fix hint. There is no silent fallback data
  source, by design.

## Fixtures (tests/offline only)

```bat
python scripts\generate_fixture.py             :: regenerate synthetic bars
copilot load-fixtures tests\fixtures\mnq_1m_sample.csv
```

Fixture bars are tagged `source='fixture'` in the DB and are synthetic — never market data.

## Layout

```
config.yaml                  all knobs: symbols, timeframes, sessions, risk gates
src/futures_copilot/
  config.py                  typed config (auto-execution is rejected at parse time)
  models.py                  Candle model (epoch-seconds + OHLC sanity enforced)
  errors.py                  typed, hinted errors
  db/                        schema.sql + Store
  data/
    base.py                  CandleSource interface
    tvmcp.py                 PRODUCT path: TradingView Desktop via MCP/CDP
    fixtures.py              TEST path: CSV fixtures
    resample.py              1m -> 3m/5m/15m/1h
    collector.py             backfill / collect / status
  cli.py                     copilot init-db|health|backfill|collect|status|load-fixtures
tests/                       35 tests, all offline
vendor/tradingview-mcp/      pinned bridge source (run npm install once)
```

## Safety invariants (enforced in code, not just documented)

- `auto_execution_enabled: true` in config fails validation and refuses to load.
- The MCP adapter has a tool allowlist: chart reading/navigation only — no alerts,
  drawing, UI automation, replay-trading, or anything order-like.
- Final `signals.decision` (later phases) is written by the Python risk gate only;
  the Claude layer's output schema has no decision field.

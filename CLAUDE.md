# Futures AI Chart Copilot — Claude working rules

Manual paper-trading decision-support copilot for MNQ/MES.
**Python calculates. Claude explains. Human approves. WAIT is the default.**

## Hard safety rules (never relax, never make configurable)
- No broker execution, no live orders, no autonomous trading, no Robinhood integration.
- No Telegram/Discord alerts, no paid services, no Yahoo/yfinance, no screenshot trade guessing.
- `signals.decision` is written by the risk gate ONLY. Claude/packet layer has NO writable decision field.
- Config validators reject `auto_execution_enabled=true` and `manual_approval_required=false`.
- Fixtures (`source='fixture'`) are for tests/offline dev only — never a live-data fallback.
- News filter is manual config only (no calendar scraping).

## Data path
TradingView Desktop (CME package) → CDP :9222 → vendored `tradesdontlie/tradingview-mcp`
(`vendor/tradingview-mcp`, pinned in `vendor/PINNED_COMMIT.txt`) → SQLite `data/copilot.db`.
`data_get_ohlcv` returns newest ≤500 bars, so backfill pulls 1m/5m/15m/1h natively;
collect loop accumulates 1m and resamples derived TFs for newer windows only.

## Conventions
- All stored timestamps: bar OPEN time, UTC epoch seconds. Sessions are wall-clock America/New_York.
- Trading day = 18:00 ET → 17:00 ET next day, labeled by END date.
- No lookahead anywhere: swings carry `confirmed_close_ts`, FVG lifecycle stamps causing-bar CLOSE,
  MSS breaks only already-confirmed swings, levels clip to the knowledge horizon.
- Levels resolve from the finest timeframe COVERING the window (1m→5m→15m→1h);
  opening range is 1m-only. Partial coverage returns None — never fabricate a level.

## Commands
```
copilot health | backfill | collect [--loop] | status
copilot state --symbol MNQ [--as-of TS]
copilot scan  --symbol MNQ [--as-of TS]
copilot packet --latest
copilot journal <bias|result|review|mistakes> ...
copilot dashboard    # or: streamlit run src/futures_copilot/dashboard/app.py
```

## Dev
- Windows: `.venv\Scripts\python.exe -m pytest -q` (Python 3.14).
- Never delete tests to make progress. Commit after each green phase.
- Docs live in `docs/`; per-feature state in `features_status.json`.

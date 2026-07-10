# Futures AI Chart Copilot — v0.1

Manual paper-trading decision-support copilot for MNQ/MES.

> **Python calculates. Claude explains. Human approves.**
> No broker execution. No live orders. No autonomous trading. No alerts.
> WAIT is the default. This tool never places a trade, anywhere, ever.

## Finalization highlights

- **SQLite WAL concurrency:** readers and writers share the local store safely with a bounded busy timeout; WAL sidecar files are recovery data, never files to delete.
- **CME roll protection:** the risk gate checks both the candidate and decision-horizon trading days across the 18:00 ET boundary. The hand-verified 2026–2028 calendar falls back to the CME convention for later years, with a verification reminder whenever the next roll date is computed.
- **Paste-safe packet minification:** Claude receives only the external template plus a minified context block. Archival Markdown is explicitly not pasteable, and each written packet includes a sibling `.prompt.txt` artifact.
- **Content-addressed packet freshness:** rendered packet artifacts are cached by a SHA-256 of their real inputs, so journal, bias, prep, and corrected-market-state changes invalidate stale views automatically.
- **Obsidian integration:** when `copilot prep` builds a session cache, it reads a fixed allowlist only; inactive, superseded, and expired notes are excluded before they can reach Claude.

## The loop

```
TradingView Desktop (your CME data) ──CDP :9222──▶ vendored tradingview-mcp
      ▶ SQLite candles ▶ feature engine ▶ strategy candidates
      ▶ RISK GATE ▶ LONG / SHORT / WAIT / REJECT           ← the only decision-maker
      ▶ Claude packet (explains/grades/warns — cannot decide)
      ▶ you: approve + paper trade by hand ▶ journal ▶ memory makes packets smarter
```

## Paper-trading quick start (Windows, from the project root)

The only execution step is yours: review the deterministic gate result, then place a paper trade manually in your simulator. Never use this project to place a live order.

```bat
.venv\Scripts\python.exe -m pip install -e .[dev,dashboard]

:: start TradingView Desktop with CDP (once per session)
vendor\tradingview-mcp\scripts\launch_tv_debug.bat

.venv\Scripts\copilot health                 :: bridge + chart reachable?
.venv\Scripts\copilot backfill --symbols MNQ :: 1m/5m/15m/1h native pulls
.venv\Scripts\copilot collect --loop         :: keep 1m flowing (own terminal)

.venv\Scripts\copilot status                 :: coverage / freshness / gaps
.venv\Scripts\copilot state --symbol MNQ     :: market-state JSON (levels + provenance)
.venv\Scripts\copilot scan  --symbol MNQ     :: strategies + risk gate
.venv\Scripts\copilot packet --latest        :: writes archival JSON/Markdown + paste-safe .prompt.txt
.venv\Scripts\copilot prep --symbol MNQ      :: cache vault notes for the day (optional)
.venv\Scripts\copilot preflight --symbol MNQ :: desk-memory cache from your journal
.venv\Scripts\copilot journal show           :: journal state
.venv\Scripts\copilot dashboard              :: local Streamlit dashboard
```

In the dashboard Packet tab, copy the “Paste THIS into Claude” block (or use the `.prompt.txt` file), then follow the risk-gate decision exactly: `WAIT` and `REJECT` mean no paper trade. Record every taken or skipped setup with `copilot journal` so the next packet can surface the relevant rules and mistakes. After changing a vault note's lifecycle fields, run `copilot prep` again; prep caches are deliberately treated as safety snapshots.

Tests: `.venv\Scripts\python.exe -m pytest -q` (207 tests).

Desk Mode v0.2 (golden hour, trade governor, equal-level stop filter, vault
prep cache): see `docs/DESK_MODE.md`.

## What's inside

| Layer | Where | Notes |
|---|---|---|
| Data | `data/` | tvmcp bridge (read-only allowlist), backfill/collect, resampler; fixtures are tests-ONLY |
| Store | `db/` | SQLite; ts = bar open UTC; `source` column; signals/journal/memory tables |
| Features | `features/` | sessions, multi-TF levels w/ coverage rule, swings (confirmation lag), sweeps/reclaims, MSS/CISD, FVG lifecycle, VWAP, ATR — all as-of aware, no lookahead |
| Strategies | `strategies/` | plugin `detect(ctx) -> [SignalCandidate]`; **MTF Session Liquidity Trap** (full) + ORB skeleton (off by default); candidates ≠ decisions |
| Risk gate | `gate/` | min RR, staleness, chase, stop/target ATR caps, session + chop filters, MANUAL news blackouts, per-session/day caps; sole writer of `signals.decision` |
| Packets | `packet/` | JSON + Markdown for Claude; output schema has **no decision field** (`additionalProperties: false`) |
| Journal | CLI `journal` | bias / review / result / mistakes → SQLite |
| Memory | `memory/` | deterministic SQL similar-setup lookup; optional LOCAL vector scaffold (off by default, no network embeddings) |
| Dashboard | `dashboard/` | Streamlit, dark Legend-style; read + journal only — no execution anything |
| Vault | `trading_brain_seed/` | Obsidian starter vault (templates, playbooks, rules, mistake index) — move into your `Trading Brain` vault |

## Hard boundaries (enforced in code)

- `auto_execution_enabled: true` or `manual_approval_required: false` → config REFUSES to load
- decision written by `gate/risk_gate.py` only; Claude's output schema cannot carry one
- no Yahoo/yfinance, no paid data, no calendar scraping (news windows are typed by you in `config.yaml`)
- fixtures carry `source='fixture'` and are never a live fallback
- levels are never fabricated: window not covered by any timeframe ⇒ `null` + `level_sources` says which TF produced each level

Docs: `docs/ARCHITECTURE.md` · `docs/SAFETY.md` · `docs/TESTING.md` · `docs/PROGRESS.md` · `docs/DESK_MODE.md` · `docs/DESK_MEMORY.md` · per-feature state in `features_status.json`.

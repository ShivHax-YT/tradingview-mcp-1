# Testing

Run (Windows, user machine):
```
.venv\Scripts\python.exe -m pytest -q
```
Run (sandbox/dev): `PYTHONPATH=src python3 -m pytest -q`

SQLite tests write to pytest tmp_path (never the repo db). On network mounts,
WAL fails — Store falls back to DELETE journal mode automatically.

## Suite map
- data layer: `test_store`, `test_resample`, `test_collector`, `test_tvmcp_unit`, `test_fixture_source`
- features: `test_sessions`, `test_levels`, `test_levels_multi_tf` (coverage fallback +
  horizon clipping), `test_swings`, `test_sweeps_structure`, `test_fvg`, `test_vwap_atr_pricing`
- state: `test_market_state` (build, serialize, as-of replay, no-future-bars)
- strategy: `test_strategy_liquidity_trap` (LONG/SHORT/WAIT scenarios, no-lookahead),
  `test_strategy_orb` (skeleton, conservative)
- gate: `test_risk_gate` (every reject rule, caps, session/chop/news filters)
- packet: `test_packet` (schema, no writable decision field, md+json outputs)
- journal: `test_journal` (bias/review/result/mistake writes)
- memory: `test_memory` (similar-setup SQL lookup determinism; vector adapter interface)
- dashboard: `test_dashboard_smoke` (import + data-access smoke, no server)

## Rules
- Never delete or weaken a test to make a phase pass.
- Fixture bars only in tests (`source='fixture'`).
- Live-bridge failures must surface as typed errors — no fake success, no fixture fallback.

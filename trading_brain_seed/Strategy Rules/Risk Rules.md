---
type: rules
version: 1
tags: [rules, risk]
---

# Risk Rules (v1)

Mirrors `config.yaml` → `risk:` so the human and the gate agree.

- Min RR: **1.5** — below that the gate rejects; don't argue with it.
- Stop: ATR-buffered beyond the sweep extreme, never wider than **2.0× ATR(14, 5m)**.
- Target: next resting liquidity, at least **0.5× ATR** away.
- Freshness: signal dies **3 candles** after confirmation. Stale = gone.
- Sessions: london + ny only. Asia is for bias, not entries.
- Chop filter: day span under **2× ATR(15m)** = stand down.
- News: manual blackout windows in config — if you didn't enter it, the gate can't see it.

When a rule feels wrong, log it in [[Weekly Review]] — change config + this note together,
or change neither. Related: [[Copilot Trading Rules]]

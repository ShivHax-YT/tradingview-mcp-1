---
type: playbook
setup: session_liquidity_trap
status: active
tags: [playbook]
---

# MTF Session Liquidity Trap

**The story:** a session or prior-day level gets swept, the move fails fast,
and structure flips — the trapped side fuels the reversal.

## Sequence (what the code checks)
1. **Sweep** — price wicks through PDL/PDH or asia/london H/L in the current session.
2. **Reclaim** — a close back across the level within **3 candles** (5m).
3. **Confirmation** — MSS (close through last confirmed 5m swing) and/or CISD
   (close through the delivery series' opening price), within 12 candles.
4. **Entry** — retest of the impulse FVG (preferred) or the broken structure level.
5. **Stop** — beyond the sweep extreme + 0.25× ATR buffer.
6. **Target** — next resting liquidity on the profit side (session/PD highs for longs).

## Quality (grade drivers)
- fast reclaim (≤2 candles) · MSS **and** CISD · FVG entry · discount (long) /
  premium (short) day-range context · VWAP on your side · RR ≥ 2.

## Where it fails
- Slow reclaim → it's continuation, not a trap ([[_Mistake Index]]: `traded_through_level`)
- Chasing after the impulse without the retest (`chased_entry`)
- Asia levels during lunch chop — see [[Risk Rules]] chop filter

Log outcomes via [[Trade Review]]; the copilot's similar-setup memory groups
by sweep kind + grade + RR band, so consistent journaling literally makes the
packets smarter.

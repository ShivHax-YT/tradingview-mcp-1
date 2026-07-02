---
type: rules
version: 1
tags: [rules]
---

# Copilot Trading Rules (v1)

The non-negotiables. The risk gate enforces the mechanical ones in code; this
note exists so *you* internalize them. One change per week, max — via [[Weekly Review]].

1. **WAIT is the default.** No signal, no trade. Boredom is not a setup.
2. **The gate's decision is final.** Claude explains; it cannot upgrade a WAIT.
   Neither can you, mid-session.
3. Trade only [[MTF Session Liquidity Trap]] until 20 reviewed trades exist.
4. Confirmed closes only — no acting on a forming candle.
5. Never chase past the entry zone (gate: `entry_not_chased`).
6. Respect the caps: 2 signals/session, 3/day. Done is done.
7. News windows are manual — keep `news_blackouts` in config.yaml current ([[Risk Rules]]).
8. Every taken signal gets a [[Trade Review]] the same day.
9. Every rule bend gets a [[Mistake Log]] entry. No silent exceptions.
10. Paper only. No broker hookup exists, and none will be added.

Related: [[Risk Rules]] · [[_Mistake Index]] · [[00 Home]]

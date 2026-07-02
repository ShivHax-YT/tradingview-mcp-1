---
type: trade-review
symbol: MNQ
signal_id: 1
trading_day: "2026-07-02"
taken: true
result_r: 1.8
mistakes: [exited_early]
tags: [review, sample]
---

# Trade Review — 2026-07-02 (SAMPLE)

Example of a filled [[Trade Review]]. The graph works when these link out:
setup [[MTF Session Liquidity Trap]], mistake [[_Mistake Index]] (`exited_early`),
rules touched [[Copilot Trading Rules]].

## What happened
Gate said LONG, grade A, RR 1.96 (asia-low trap). Took it at the FVG midpoint,
stop honored, exited at +1.8R into the asia high instead of the full target.

## Lesson
`exited_early` again — second time this month. Rule update proposed in
[[Weekly Review]]: "no manual exits above +1R unless a 5m close breaks structure."

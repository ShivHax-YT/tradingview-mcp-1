---
type: index
tags: [mistake, index]
---

# Mistake Index

One note per recurring mistake (use the [[Mistake Log]] template). Seed tags
the risk gate already polices — link your reviews to these:

- `chased_entry` — entered beyond the zone; gate check `entry_not_chased`
- `stale_signal` — acted >3 candles after confirmation
- `countertrend_no_context` — long in deep premium / short in deep discount
- `oversized_stop` — widened the stop past 2× ATR "to be safe"
- `revenge_trade` — traded a WAIT after a loss
- `moved_stop` — touched the stop mid-trade
- `exited_early` — cut the winner before target without invalidation
- `traded_through_level` — called a sweep when price was just trading through

Every new mistake: create the note, link the [[Trade Review]] it came from,
write the rule update, and mirror it with `copilot journal mistake`.

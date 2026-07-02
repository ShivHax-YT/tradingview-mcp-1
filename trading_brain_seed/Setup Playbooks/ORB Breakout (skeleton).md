---
type: playbook
setup: orb_breakout
status: experimental-disabled
tags: [playbook, experimental]
---

# ORB Breakout — skeleton (disabled)

Deliberately **off** (`strategies.orb.enabled: false`). The code only knows:
confirmed 5m close beyond the 15-min opening range, then an edge retest that
holds; a close back inside cancels the story (fakeout ≠ reversal yet).

Grade is hard-capped at **C** and every candidate carries
`orb_skeleton_experimental`. Do not enable before the liquidity-trap playbook
has 20+ reviewed trades — see [[Copilot Trading Rules]] rule 3.

What v0.2 needs before this earns trust: volume/participation read, A/B/C day
typing, midpoint magnet behavior, fakeout-reversal handling.

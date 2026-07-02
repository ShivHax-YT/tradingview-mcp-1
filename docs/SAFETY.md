# Safety model

This project is decision support for MANUAL paper trading. It must never grow
execution capability. The boundaries below are enforced in code, not just policy.

## Enforced in code
| Rule | Enforcement |
|---|---|
| No auto execution | `RiskConfig` validator raises on `auto_execution_enabled=true` |
| Manual approval always | `RiskConfig` validator raises on `manual_approval_required=false` |
| Decision authority | `signals.decision` written only by `gate/risk_gate.py`; packet/Claude schema has no decision field |
| Read-only bridge | tvmcp adapter allowlists read-only MCP tools (`data_get_ohlcv`, health) |
| No fake data | fixtures carry `source='fixture'`; live paths refuse them; no silent fallback source |
| No fabricated levels | window not covered by any timeframe ⇒ level is None |
| No lookahead | confirmation timestamps + as-of filtering throughout the feature engine |

## Out of scope permanently
Broker APIs, order routing, position management, alert bots (Telegram/Discord),
paid data services, autonomous trading loops, screenshot-based trade guessing.

## Decision flow
candles → features → strategy candidates → **risk gate** (LONG/SHORT/WAIT/REJECT)
→ prompt packet → Claude may explain/grade/warn/journal → **human** decides and
manually paper-trades. Claude cannot upgrade a WAIT/REJECT or alter levels.

## News risk
`risk.news_blackouts` in config.yaml is a manually maintained list of UTC windows.
No calendar scraping, no third-party feeds. If you don't maintain it, the gate
cannot see news risk — that is your responsibility.

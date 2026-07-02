"""Strategy engine: build context from stored bars, run enabled strategies,
persist candidates. Candidates are NOT decisions — see gate/risk_gate.py."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..config import Config
from ..db.store import Store
from ..features.market_state import MarketState, build_market_state
from .base import SignalCandidate, Strategy, StrategyContext
from .liquidity_trap import TF_SECONDS, SessionLiquidityTrap
from .orb import OrbBreakout

REGISTRY: dict[str, type[Strategy]] = {
    SessionLiquidityTrap.name: SessionLiquidityTrap,
    OrbBreakout.name: OrbBreakout,
}


@dataclass
class ScanResult:
    state: MarketState
    candidates: list[SignalCandidate] = field(default_factory=list)
    state_id: int | None = None

    def to_dict(self) -> dict:
        return {
            "market_state": self.state.model_dump(),
            "candidates": [c.model_dump() for c in self.candidates],
        }


def build_context(store: Store, config: Config, symbol: str, as_of_ts: int | None = None) -> StrategyContext:
    state = build_market_state(store, config, symbol, as_of_ts=as_of_ts)
    horizon = state.as_of_close_ts
    dfs = {}
    for tf in config.timeframes.canonical:
        df = store.get_candles_df(symbol, tf)
        tf_s = TF_SECONDS.get(tf)
        if tf_s is None:
            continue
        # CLOSED bars only: a bar is knowable once its close time <= horizon.
        dfs[tf] = df[df["ts"] + tf_s <= horizon].reset_index(drop=True)
    return StrategyContext(
        symbol=symbol, spec=config.symbols[symbol], config=config,
        state=state, dfs=dfs, horizon_ts=horizon,
    )


def enabled_strategies(config: Config) -> list[Strategy]:
    out: list[Strategy] = []
    for name in config.strategies.enabled:
        cls = REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"unknown strategy {name!r}; known: {sorted(REGISTRY)}")
        out.append(cls())
    return out


def scan(store: Store, config: Config, symbol: str, as_of_ts: int | None = None,
         persist: bool = True) -> ScanResult:
    ctx = build_context(store, config, symbol, as_of_ts=as_of_ts)
    candidates: list[SignalCandidate] = []
    for strat in enabled_strategies(config):
        candidates.extend(strat.detect(ctx))
    result = ScanResult(state=ctx.state, candidates=candidates)
    if persist:
        result.state_id = store.save_market_state(
            symbol, ctx.state.ts, ctx.state.session, ctx.state.current_price,
            ctx.state.to_json(),
        )
        for c in candidates:
            store.save_strategy_output(
                symbol, c.ts, c.strategy, c.direction, c.grade,
                json.dumps(c.model_dump(), default=str),
            )
    return result

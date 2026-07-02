"""Strategy plugin interface.

A strategy is a pure detector: StrategyContext in, SignalCandidate list out.
Candidates are NOT trade decisions — the risk gate (Phase 4) is the only
component that may emit LONG/SHORT/WAIT/REJECT. Strategies must:

- never read the network or the DB (everything arrives in the context)
- never see bars beyond the context horizon (closed bars only)
- output honest geometry: entry zone, stop, target, all validated
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field, model_validator

from ..config import Config, SymbolSpec
from ..features.market_state import MarketState

DIRECTIONS = ("long", "short")
GRADES = ("A", "B", "C")


class SignalCandidate(BaseModel):
    """A detected setup, pre-risk-gate. Geometry is validated at construction:
    stop and target must sit on the correct sides of the entry for direction."""

    strategy: str
    setup_type: str
    symbol: str
    direction: str                    # 'long' | 'short'
    ts: int                           # open time of the confirmation bar
    confirmed_close_ts: int           # when this candidate became knowable
    detection_timeframe: str          # e.g. '5m'
    session: str | None

    entry_lo: float                   # entry zone (retest zone), lo <= hi
    entry_hi: float
    entry_ref: float                  # preferred entry inside the zone
    stop: float
    target: float
    target_name: str
    tp2: float | None = None
    tp2_name: str | None = None
    rr: float
    grade: str

    confluences: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _sane(self) -> "SignalCandidate":
        if self.direction not in DIRECTIONS:
            raise ValueError(f"direction must be one of {DIRECTIONS}")
        if self.grade not in GRADES:
            raise ValueError(f"grade must be one of {GRADES}")
        if self.entry_lo > self.entry_hi:
            raise ValueError(f"entry zone inverted: {self.entry_lo} > {self.entry_hi}")
        if not (self.entry_lo <= self.entry_ref <= self.entry_hi):
            raise ValueError("entry_ref outside entry zone")
        if self.direction == "long":
            if not (self.stop < self.entry_ref < self.target):
                raise ValueError(
                    f"long geometry invalid: need stop {self.stop} < entry {self.entry_ref} < target {self.target}"
                )
        else:
            if not (self.target < self.entry_ref < self.stop):
                raise ValueError(
                    f"short geometry invalid: need target {self.target} < entry {self.entry_ref} < stop {self.stop}"
                )
        if self.rr <= 0:
            raise ValueError(f"rr must be positive, got {self.rr}")
        return self


@dataclass
class StrategyContext:
    """Everything a strategy may look at. dfs contain CLOSED bars only,
    filtered to close_ts <= horizon_ts by the engine."""

    symbol: str
    spec: SymbolSpec
    config: Config
    state: MarketState
    dfs: dict[str, pd.DataFrame]
    horizon_ts: int                   # knowledge horizon (close of newest 1m bar / as_of)
    extras: dict[str, Any] = field(default_factory=dict)


class Strategy(ABC):
    """Plugin base. Implementations must be deterministic and side-effect free."""

    name: str = "abstract"
    setup_type: str = "abstract"

    @abstractmethod
    def detect(self, ctx: StrategyContext) -> list[SignalCandidate]:
        """Return zero or more candidates knowable at ctx.horizon_ts."""
        raise NotImplementedError

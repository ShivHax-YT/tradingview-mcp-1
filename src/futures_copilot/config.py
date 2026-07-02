"""Typed configuration loading (config.yaml -> pydantic models)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from .errors import ConfigError


class SymbolSpec(BaseModel):
    chart_symbol: str
    tick_size: float
    tick_value: float


class TimeframesConfig(BaseModel):
    canonical: list[str]
    tradingview_map: dict[str, str]

    @field_validator("tradingview_map")
    @classmethod
    def _covers_canonical(cls, v: dict[str, str], info) -> dict[str, str]:
        canonical = info.data.get("canonical", [])
        missing = [tf for tf in canonical if tf not in v]
        if missing:
            raise ValueError(f"tradingview_map missing canonical timeframes: {missing}")
        return v


class TvMcpConfig(BaseModel):
    node_command: str = "node"
    server_path: str = "vendor/tradingview-mcp/src/server.js"
    cdp_port: int = 9222
    request_timeout_s: int = 30
    max_bars_per_call: int = 500


class BackfillConfig(BaseModel):
    timeframes: list[str] = Field(default_factory=lambda: ["1m", "5m", "15m", "1h"])
    bars_per_timeframe: int = 500


class CollectConfig(BaseModel):
    timeframe: str = "1m"
    interval_s: int = 30
    drop_unclosed_last_bar: bool = True


class DataConfig(BaseModel):
    source: str = "tvmcp"
    tvmcp: TvMcpConfig = Field(default_factory=TvMcpConfig)
    backfill: BackfillConfig = Field(default_factory=BackfillConfig)
    collect: CollectConfig = Field(default_factory=CollectConfig)


class SessionsConfig(BaseModel):
    trading_day_start: str = "18:00"
    maintenance_break: list[str] = Field(default_factory=lambda: ["17:00", "18:00"])
    asia: list[str] = Field(default_factory=lambda: ["18:00", "03:00"])
    london: list[str] = Field(default_factory=lambda: ["03:00", "09:30"])
    ny: list[str] = Field(default_factory=lambda: ["09:30", "16:00"])
    opening_range_minutes: int = 15


class FeaturesConfig(BaseModel):
    swing_k: int = 2                 # fractal half-width; swing confirmed k bars later
    atr_period: int = 14
    fvg_lookback_bars: int = 200     # how far back to scan 5m FVGs for market state
    max_fvgs_in_state: int = 5


class RiskConfig(BaseModel):
    manual_approval_required: bool = True
    auto_execution_enabled: bool = False
    min_rr: float = 1.5
    max_signals_per_session: int = 2
    max_signals_per_day: int = 3
    signal_expiry_candles: int = 3
    reclaim_max_candles: int = 3
    max_stop_atr_mult: float = 2.0
    max_entry_distance_atr_mult: float = 0.5

    @field_validator("auto_execution_enabled")
    @classmethod
    def _never_auto(cls, v: bool) -> bool:
        if v:
            raise ValueError(
                "auto_execution_enabled=true is not permitted in this project. "
                "This is a manual paper-trading copilot; execution is out of scope permanently."
            )
        return v

    @field_validator("manual_approval_required")
    @classmethod
    def _always_manual(cls, v: bool) -> bool:
        if not v:
            raise ValueError("manual_approval_required=false is not permitted in this project.")
        return v


class AppConfig(BaseModel):
    db_path: str = "data/copilot.db"
    timezone: str = "America/New_York"


class Config(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    symbols: dict[str, SymbolSpec]
    timeframes: TimeframesConfig
    data: DataConfig = Field(default_factory=DataConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)

    # Directory containing config.yaml; relative paths resolve against it.
    root: Path = Field(default=Path("."))

    def resolve(self, rel: str | Path) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (self.root / p)

    @property
    def db_file(self) -> Path:
        return self.resolve(self.app.db_path)


def load_config(path: str | Path = "config.yaml") -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"config file not found: {path}",
            hint="run from the project root, or pass --config <path/to/config.yaml>",
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        cfg = Config(**raw, root=path.resolve().parent)
    except ValidationError as e:
        raise ConfigError(f"invalid config: {e}", hint=f"fix {path}") from e
    return cfg

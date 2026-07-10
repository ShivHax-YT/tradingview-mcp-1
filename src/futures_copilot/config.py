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
    fvg_min_ticks: int = Field(default=4, ge=1)


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
    max_live_data_latency_s: float = Field(default=5.0, gt=0)


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
    # k=3 deliberately rejects narrow two-bar noise pivots from MSS reference use.
    swing_k: int = 3                 # fractal half-width; swing confirmed k bars later
    atr_period: int = 14
    fvg_lookback_bars: int = 200     # how far back to scan 5m FVGs for market state
    max_fvgs_in_state: int = 5


class OrbConfig(BaseModel):
    enabled: bool = False            # skeleton stays off until explicitly enabled
    confirm_timeframe: str = "5m"
    retest_max_candles: int = 6


class StrategiesConfig(BaseModel):
    enabled: list[str] = Field(default_factory=lambda: ["session_liquidity_trap"])
    detection_timeframe: str = "5m"
    lookback_bars: int = 96          # scan window on the detection timeframe (~8h of 5m)
    confirm_max_candles: int = 12    # sweep -> MSS/CISD confirmation budget
    stop_buffer_atr_mult: float = 0.25
    entry_zone_atr_mult: float = 0.15
    min_target_rr_for_grade_a: float = 2.0
    max_candidates_per_scan: int = 3
    orb: OrbConfig = Field(default_factory=OrbConfig)


class NewsBlackout(BaseModel):
    """One MANUALLY-maintained news window. start is wall-clock America/New_York."""
    start: str                        # "YYYY-MM-DD HH:MM"
    minutes: int = 15
    label: str = ""


class RiskConfig(BaseModel):
    manual_approval_required: bool = True
    auto_execution_enabled: bool = False
    min_rr: float = 1.5
    max_signals_per_session: int = 2
    max_signals_per_day: int = 3
    signal_expiry_candles: int = 3
    max_feed_staleness_s: int = Field(default=180, gt=0)
    reclaim_max_candles: int = 3
    max_stop_atr_mult: float = 2.0
    max_entry_distance_atr_mult: float = 0.5
    min_target_atr_mult: float = 0.5          # target closer than this*ATR rejects
    chop_min_day_range_atr_mult: float = 2.0  # day span below this*ATR15 = chop
    allowed_sessions: list[str] = Field(default_factory=lambda: ["ny"])  # Desk Mode: NY only
    min_actionable_grades: list[str] = Field(default_factory=lambda: ["A", "B"])

    @field_validator("min_actionable_grades")
    @classmethod
    def _valid_actionable_grades(cls, v: list[str]) -> list[str]:
        invalid = [grade for grade in v if grade not in {"A", "B", "C"}]
        if invalid or not v:
            raise ValueError("min_actionable_grades must be a non-empty subset of A, B, C")
        return list(dict.fromkeys(v))
    news_blackouts: list[NewsBlackout] = Field(default_factory=list)

    # ── Desk Mode v0.2 ───────────────────────────────────────────────────────
    # Golden hour: actionable candidates only inside this ET window.
    # Start is inclusive, end is exclusive (09:30 passes, 11:00 rejects).
    enforce_golden_hour: bool = True
    golden_hour: list[str] = Field(default_factory=lambda: ["09:30", "11:00"])
    # Trade governor over JOURNALED results (trade_reviews joined to signals of
    # the same trading day). Skipped trades and scratch/zero results count as
    # neither win nor loss.
    stop_on_first_win: bool = True
    stop_after_losses: int = 2
    # Equal high/low stop-magnet filter: reject when >=2 recent detection-TF
    # bars have lows (long) / highs (short) within tolerance of the stop.
    reject_equal_level_stop_magnets: bool = True
    equal_level_tolerance_ticks: int = 3
    equal_level_lookback_bars: int = 50

    @field_validator("golden_hour")
    @classmethod
    def _golden_hour_shape(cls, v: list[str]) -> list[str]:
        if len(v) != 2:
            raise ValueError('golden_hour must be ["HH:MM", "HH:MM"]')
        for s in v:
            h, _, m = s.partition(":")
            if not (h.isdigit() and m.isdigit() and 0 <= int(h) < 24 and 0 <= int(m) < 60):
                raise ValueError(f"golden_hour entry {s!r} is not HH:MM")
        return v

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


class PreflightConfig(BaseModel):
    """Desk Memory / Preflight (v0.3): deterministic journal memory built from
    SQLite BEFORE a session. Not model training — statistics and reminders.
    Never runs in the live scan loop; never reads Obsidian (that is `prep`)."""
    enabled: bool = True
    memory_dir: str = "data/session_memory"
    lookback_reviews: int = 50       # newest journaled reviews considered
    lookback_days: int = 20          # ... within this many trading days
    top_mistakes: int = 5            # mistake tags surfaced
    min_samples_for_pattern: int = 3 # smaller groups are noise, not patterns


class VaultConfig(BaseModel):
    """Obsidian vault used ONLY by `copilot prep` (session-prep cache).
    The live scan/risk gate NEVER reads the vault — Desk Mode latency rule."""
    path: str | None = None              # vault root; None disables `copilot prep`
    session_prep_dir: str = "data/session_prep"
    max_chars_per_note: int = 4000       # keep the cache compact


class VectorMemoryConfig(BaseModel):
    enabled: bool = False            # v0.1 default: SQL memory only
    backend: str = "memory"          # memory (in-process) | chroma (optional local dep)
    path: str = "data/vector_memory"
    top_k: int = 5


class MemoryConfig(BaseModel):
    vector: VectorMemoryConfig = Field(default_factory=VectorMemoryConfig)


class SlippageConfig(BaseModel):
    """Conservative adverse fills expressed in contract ticks."""

    entry_ticks: int = 1
    stop_ticks: int = Field(default=2, ge=2)
    target_ticks: int = 1


class BacktestConfig(BaseModel):
    slippage: dict[str, SlippageConfig] = Field(default_factory=lambda: {
        "MNQ": SlippageConfig(entry_ticks=1, stop_ticks=2, target_ticks=1),
        "MES": SlippageConfig(entry_ticks=1, stop_ticks=2, target_ticks=1),
    })


class AppConfig(BaseModel):
    db_path: str = "data/copilot.db"
    timezone: str = "America/New_York"
    packets_dir: str = "packets"


class Config(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    symbols: dict[str, SymbolSpec]
    timeframes: TimeframesConfig
    data: DataConfig = Field(default_factory=DataConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    strategies: StrategiesConfig = Field(default_factory=StrategiesConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    vault: VaultConfig = Field(default_factory=VaultConfig)
    preflight: PreflightConfig = Field(default_factory=PreflightConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)

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

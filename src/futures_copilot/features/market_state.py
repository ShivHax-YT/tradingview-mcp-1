"""Assemble a structured MarketState snapshot from stored candles.

This is the JSON that later phases hand to strategy modules and (after the
risk gate) to Claude. Everything in it is deterministic and as-of aware:
pass as_of_ts to reconstruct what was knowable at any historical moment
(the replay/backtest path); default is the latest stored bar.

Data-depth note: live collection holds only ~500 bars per timeframe, so 1m
rarely reaches back to the prior day. Session/day/prior-day levels therefore
resolve from the FINEST timeframe that covers each window (1m -> 5m -> 15m -> 1h);
`level_sources` reports which timeframe produced each level so nothing is
silently coarse. The opening range is 1m-only by design.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from ..config import Config
from ..data.resample import TF_SECONDS
from ..db.store import Store
from ..errors import DataSourceError
from ..utils.roll_dates import is_in_roll_window
from .fvg import detect_fvgs
from .levels import LEVEL_TF_PREFERENCE, multi_tf_day_high_low, multi_tf_prior_day_high_low, multi_tf_session_high_low, opening_range
from .pricing import position_in_range, premium_discount
from .sessions import session_at, trading_day
from .swings import find_swings, swings_confirmed_by
from .volatility import atr
from .vwap import day_vwap

TF_SECONDS_5M = 300


class LevelPair(BaseModel):
    high: float
    low: float


class MarketState(BaseModel):
    symbol: str
    ts: int                          # open time of the newest 1m bar used
    as_of_close_ts: int              # that bar's close time — the knowledge horizon
    current_price: float
    trading_day: str
    session: str | None
    session_levels: dict[str, LevelPair | None]
    prior_day: LevelPair | None
    opening_range: LevelPair | None
    level_sources: dict[str, str | None] = Field(default_factory=dict)  # which tf produced each level
    vwap: float | None
    vwap_position: str | None        # above | below
    atr_5m: float | None
    atr_15m: float | None
    premium_discount_day: str | None
    day_range_position: float | None
    last_swing_high_5m: dict[str, Any] | None
    last_swing_low_5m: dict[str, Any] | None
    open_fvgs_5m: list[dict[str, Any]] = Field(default_factory=list)
    bar_counts: dict[str, int] = Field(default_factory=dict)
    reliable: bool = True
    reason: str | None = None

    def to_json(self) -> str:
        return json.dumps(self.model_dump(), indent=2, default=str)


def _df_as_of(
    store: Store,
    symbol: str,
    tf: str,
    as_of_ts: int | None,
    *,
    horizon_ts: int | None = None,
):
    df = store.get_candles_df(symbol, tf)
    if as_of_ts is not None and not df.empty:
        if horizon_ts is None or tf == "1m":
            df = df[df["ts"] <= as_of_ts]
        else:
            tf_seconds = TF_SECONDS.get(tf)
            if tf_seconds is not None:
                df = df[df["ts"] + tf_seconds <= horizon_ts]
    return df


def build_market_state(store: Store, config: Config, symbol: str, as_of_ts: int | None = None) -> MarketState:
    scfg = config.sessions
    fcfg = config.features

    df1 = _df_as_of(store, symbol, "1m", as_of_ts)
    if df1.empty:
        raise DataSourceError(
            f"no 1m bars stored for {symbol}" + (f" at/before {as_of_ts}" if as_of_ts else ""),
            hint="run `copilot backfill` / `copilot collect` first",
        )
    last = df1.iloc[-1]
    last_ts = int(last["ts"])
    horizon = last_ts + 60
    filter_as_of = last_ts if as_of_ts is None else as_of_ts
    price = float(last["close"])
    day = trading_day(last_ts, scfg)
    roll_window = is_in_roll_window(day)

    # Multi-timeframe frames, all as-of filtered — the fallback can never see future bars.
    dfs = {
        tf: _df_as_of(store, symbol, tf, filter_as_of, horizon_ts=horizon)
        for tf in LEVEL_TF_PREFERENCE
    }
    dfs["1m"] = df1

    level_sources: dict[str, str | None] = {}

    # Session levels: finest covering timeframe, clipped at the knowledge horizon.
    sess_levels: dict[str, LevelPair | None] = {}
    for name in ("asia", "london", "ny"):
        res = multi_tf_session_high_low(dfs, day, name, scfg, horizon_ts=horizon)
        if res:
            (hi, lo), tf_used = res
            sess_levels[name] = LevelPair(high=hi, low=lo)
            level_sources[name] = tf_used
        else:
            sess_levels[name] = None
            level_sources[name] = None

    # Prior trading day: complete past window.
    pd_res = multi_tf_prior_day_high_low(dfs, day, scfg)
    prior = LevelPair(high=pd_res[0][0], low=pd_res[0][1]) if pd_res else None
    level_sources["prior_day"] = pd_res[1] if pd_res else None

    # Opening range: 1m ONLY (precision matters), and only once the window elapsed.
    or_hl = opening_range(df1, day, scfg, as_of_ts=horizon)
    level_sources["opening_range"] = "1m" if or_hl else None

    # Developing day range (for premium/discount).
    d_res = multi_tf_day_high_low(dfs, day, scfg, horizon_ts=horizon)
    level_sources["day_range"] = d_res[1] if d_res else None

    # VWAP anchored to trading day (1m; NULL if 1m doesn't reach the day start... it
    # still computes from what exists — VWAP is cumulative, partial anchor is flagged
    # by comparing bar_counts vs session coverage in later phases).
    vwap_series = day_vwap(df1, day, scfg)
    vwap_val = float(vwap_series.iloc[-1]) if len(vwap_series) else None
    vwap_pos = None if vwap_val is None else ("above" if price >= vwap_val else "below")

    # ATR on 5m / 15m
    df5 = dfs.get("5m")
    df15 = dfs.get("15m")
    atr5 = float(atr(df5, fcfg.atr_period).iloc[-1]) if df5 is not None and len(df5) > fcfg.atr_period else None
    atr15 = float(atr(df15, fcfg.atr_period).iloc[-1]) if df15 is not None and len(df15) > fcfg.atr_period else None

    # Premium/discount vs today's developing range
    prem = pos = None
    if d_res and d_res[0][0] > d_res[0][1]:
        d_hi, d_lo = d_res[0]
        prem = premium_discount(price, d_lo, d_hi)
        pos = position_in_range(price, d_lo, d_hi)

    # 5m structure context (confirmed swings only — no lookahead)
    swing_hi = swing_lo = None
    fvg_dicts: list[dict[str, Any]] = []
    if df5 is not None and len(df5) >= 2 * fcfg.swing_k + 1:
        recent5 = df5.tail(fcfg.fvg_lookback_bars)
        swings = swings_confirmed_by(
            find_swings(recent5, fcfg.swing_k, TF_SECONDS_5M), horizon
        )
        highs = [s for s in swings if s.kind == "high"]
        lows = [s for s in swings if s.kind == "low"]
        if highs:
            swing_hi = {"price": highs[-1].price, "ts": highs[-1].bar_ts}
        if lows:
            swing_lo = {"price": lows[-1].price, "ts": lows[-1].bar_ts}

        for g in detect_fvgs(recent5, TF_SECONDS_5M):
            st = g.status(horizon)
            if st in ("open", "mitigated"):
                fvg_dicts.append(
                    {
                        "kind": g.kind, "top": g.top, "bottom": g.bottom,
                        "status": st, "created_ts": g.created_bar_ts,
                    }
                )
        fvg_dicts = fvg_dicts[-fcfg.max_fvgs_in_state:]

    return MarketState(
        symbol=symbol,
        ts=last_ts,
        as_of_close_ts=horizon,
        current_price=price,
        trading_day=day.isoformat(),
        session=session_at(last_ts, scfg),
        session_levels=sess_levels,
        prior_day=prior,
        opening_range=LevelPair(high=or_hl[0], low=or_hl[1]) if or_hl else None,
        level_sources=level_sources,
        vwap=vwap_val,
        vwap_position=vwap_pos,
        atr_5m=atr5,
        atr_15m=atr15,
        premium_discount_day=prem,
        day_range_position=pos,
        last_swing_high_5m=swing_hi,
        last_swing_low_5m=swing_lo,
        open_fvgs_5m=fvg_dicts,
        bar_counts={
            tf: int(len(_df_as_of(store, symbol, tf, filter_as_of, horizon_ts=horizon)))
            for tf in config.timeframes.canonical
        },
        reliable=not roll_window,
        reason="roll_window" if roll_window else None,
    )

"""ORB (Opening Range Breakout) — SKELETON ONLY, disabled by default.

Deliberately conservative v0.1 behavior:
- returns [] unless `strategies.orb.enabled: true` in config.yaml
- only emits BREAKOUT-with-retest candidates; a detected fakeout (close back
  inside the range after the break) CANCELS the story rather than flipping it
- grade is hard-capped at "C" and every candidate carries an explicit
  experimental warning. The risk gate treats it like any other candidate —
  it gets no special treatment.

The full breakout/fakeout playbook (volume, A/B/C day typing, midpoint magnet)
is a later module; this skeleton exists so the plugin interface, config gating
and tests are real.
"""

from __future__ import annotations

import pandas as pd

from ..features.pricing import rr as rr_of
from ..features.sessions import opening_range_bounds
from ..features.volatility import atr
from .base import SignalCandidate, Strategy, StrategyContext
from .liquidity_trap import TF_SECONDS, _target_levels


class OrbBreakout(Strategy):
    name = "orb_breakout"
    setup_type = "orb_breakout"

    def detect(self, ctx: StrategyContext) -> list[SignalCandidate]:
        scfg = ctx.config.strategies
        if not scfg.orb.enabled:
            return []
        st = ctx.state
        if st.session != "ny" or st.opening_range is None:
            return []

        tf = scfg.orb.confirm_timeframe
        tf_s = TF_SECONDS[tf]
        df = ctx.dfs.get(tf)
        if df is None or len(df) < 5:
            return []
        day = pd.Timestamp(st.trading_day).date()
        _, or_end = opening_range_bounds(day, ctx.config.sessions)
        after = df[df["ts"] >= or_end].reset_index(drop=True)
        if after.empty:
            return []

        atr_series = atr(df, ctx.config.features.atr_period)
        cur_atr = float(atr_series.iloc[-1]) if len(atr_series) else None
        if not cur_atr or cur_atr <= 0:
            return []

        or_hi, or_lo = st.opening_range.high, st.opening_range.low
        out: list[SignalCandidate] = []
        for direction, edge, beyond in (("long", or_hi, "above"), ("short", or_lo, "below")):
            cand = self._breakout_with_retest(ctx, after, direction, edge, or_hi, or_lo, tf_s, cur_atr)
            if cand is not None:
                out.append(cand)
        return out

    def _breakout_with_retest(
        self, ctx: StrategyContext, df: pd.DataFrame, direction: str,
        edge: float, or_hi: float, or_lo: float, tf_s: int, cur_atr: float,
    ) -> SignalCandidate | None:
        scfg = ctx.config.strategies
        closes = df["close"].to_numpy()
        lows = df["low"].to_numpy()
        highs = df["high"].to_numpy()
        ts = df["ts"].to_numpy()

        # 1. first confirmed CLOSE beyond the edge
        brk = None
        for i in range(len(df)):
            if direction == "long" and closes[i] > edge:
                brk = i
                break
            if direction == "short" and closes[i] < edge:
                brk = i
                break
        if brk is None:
            return None

        # 2. fakeout guard + retest search, within retest_max_candles
        retest = None
        end = min(brk + 1 + scfg.orb.retest_max_candles, len(df))
        for j in range(brk + 1, end):
            back_inside = closes[j] < edge if direction == "long" else closes[j] > edge
            if back_inside:
                return None                     # fakeout: story cancelled, not reversed (v0.1)
            touched = lows[j] <= edge if direction == "long" else highs[j] >= edge
            if touched:
                retest = j
                break
        if retest is None:
            return None                         # no retest -> chasing; skeleton stays out

        pad = scfg.entry_zone_atr_mult * cur_atr
        entry_ref = float(edge)
        entry_lo, entry_hi = entry_ref - pad, entry_ref + pad
        or_mid = (or_hi + or_lo) / 2.0
        buf = scfg.stop_buffer_atr_mult * cur_atr
        stop = or_mid - buf if direction == "long" else or_mid + buf

        targets = _target_levels(ctx, direction)
        if direction == "long":
            reachable = sorted([(n, p) for n, p in targets if p > entry_hi + 1e-9], key=lambda t: t[1])
        else:
            reachable = sorted([(n, p) for n, p in targets if p < entry_lo - 1e-9], key=lambda t: -t[1])
        if not reachable:
            return None
        t_name, t_price = reachable[0]
        try:
            rr = rr_of(entry_ref, stop, t_price)
        except ValueError:
            return None

        conf_ts = int(ts[retest])
        return SignalCandidate(
            strategy=self.name, setup_type=self.setup_type, symbol=ctx.symbol,
            direction=direction, ts=conf_ts, confirmed_close_ts=conf_ts + tf_s,
            detection_timeframe=scfg.orb.confirm_timeframe, session=ctx.state.session,
            entry_lo=entry_lo, entry_hi=entry_hi, entry_ref=entry_ref,
            stop=float(stop), target=float(t_price), target_name=t_name,
            rr=float(rr), grade="C",
            confluences=[f"or_breakout_{direction}", "edge_retest"],
            warnings=["orb_skeleton_experimental"],
            context={
                "or_high": or_hi, "or_low": or_lo,
                "breakout_ts": int(ts[brk]), "retest_ts": conf_ts, "atr": cur_atr,
            },
        )

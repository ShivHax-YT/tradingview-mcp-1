"""Strategy Module 1 — MTF Session Liquidity Trap.

Narrative (long side; short is mirrored):
  1. A watched liquidity level (prior-day low, asia/london session low) is
     SWEPT — price wicks below it during the current session.
  2. Price RECLAIMS the level: a candle closes back above within
     `risk.reclaim_max_candles` candles of the sweep.
  3. Structure CONFIRMS the reversal: MSS (close above the last confirmed 5m
     swing high) and/or CISD (close above the open of the down-close delivery
     series) within `strategies.confirm_max_candles` of the sweep.
  4. Entry is the RETEST: a same-direction FVG created during the
     sweep-to-confirmation impulse (preferred), else the broken structure level.
  5. Stop is ATR-aware below the sweep extreme; target is the NEXT liquidity
     level; RR is computed, context (premium/discount, VWAP) grades the setup.

Everything runs on closed bars at the context horizon — no lookahead.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..features.fvg import detect_fvgs
from ..features.pricing import premium_discount, rr as rr_of
from ..features.sessions import session_bounds
from ..features.structure import detect_cisd, detect_mss
from ..features.sweeps import detect_reclaim, detect_sweep
from ..features.swings import find_swings
from ..features.volatility import atr
from .base import SignalCandidate, Strategy, StrategyContext

TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "1h": 3600}


def _watch_levels(ctx: StrategyContext) -> list[dict[str, Any]]:
    """Liquidity levels to watch: prior day H/L + COMPLETED sessions' H/L.
    The current session's own developing extremes are not trap levels."""
    st = ctx.state
    out: list[dict[str, Any]] = []
    if st.prior_day is not None:
        out.append({"name": "prior_day_low", "price": st.prior_day.low, "side": "sell"})
        out.append({"name": "prior_day_high", "price": st.prior_day.high, "side": "buy"})
    order = ("asia", "london", "ny")
    cur = st.session
    cur_i = order.index(cur) if cur in order else len(order)
    for name in order[:cur_i]:
        lp = st.session_levels.get(name)
        if lp is not None:
            out.append({"name": f"{name}_low", "price": lp.low, "side": "sell"})
            out.append({"name": f"{name}_high", "price": lp.high, "side": "buy"})
    return out


def _target_levels(ctx: StrategyContext, direction: str) -> list[tuple[str, float]]:
    """Liquidity a trade can REACH FOR: resting liquidity on the profit side.
    Longs target buy-side liquidity (session/prior-day HIGHS), shorts target
    sell-side (LOWS). VWAP counts when it sits on the profit side (the > / <
    entry filter at the call site enforces that). Opening-range edges are NOT
    trap targets — they belong to the ORB playbook."""
    want = "buy" if direction == "long" else "sell"
    out = [(lv["name"], float(lv["price"])) for lv in _watch_levels(ctx) if lv["side"] == want]
    if ctx.state.vwap is not None:
        out.append(("vwap", float(ctx.state.vwap)))
    return out


class SessionLiquidityTrap(Strategy):
    name = "session_liquidity_trap"
    setup_type = "session_liquidity_trap"

    def detect(self, ctx: StrategyContext) -> list[SignalCandidate]:
        scfg = ctx.config.strategies
        rcfg = ctx.config.risk
        tf = scfg.detection_timeframe
        tf_s = TF_SECONDS[tf]
        df = ctx.dfs.get(tf)
        if df is None or len(df) < 10:
            return []
        df = df.tail(scfg.lookback_bars).reset_index(drop=True)

        st = ctx.state
        if st.session is None:
            return []                      # 16:00-18:00 dead zone: stand down
        day = pd.Timestamp(st.trading_day).date()
        sess_start, _ = session_bounds(day, st.session, ctx.config.sessions)

        atr_series = atr(df, ctx.config.features.atr_period)
        cur_atr = float(atr_series.iloc[-1]) if len(atr_series) else None
        if not cur_atr or cur_atr <= 0:
            return []

        swings = find_swings(df, ctx.config.features.swing_k, tf_s)
        candidates: list[SignalCandidate] = []

        for lv in _watch_levels(ctx):
            cand = self._detect_level(ctx, df, lv, sess_start, tf, tf_s, cur_atr, swings)
            if cand is not None:
                candidates.append(cand)

        candidates.sort(key=lambda c: (c.grade, -c.rr))
        return candidates[: scfg.max_candidates_per_scan]

    # ── single-level pipeline ────────────────────────────────────────────────
    def _detect_level(
        self, ctx: StrategyContext, df: pd.DataFrame, lv: dict[str, Any],
        sess_start: int, tf: str, tf_s: int, cur_atr: float, swings,
    ) -> SignalCandidate | None:
        scfg = ctx.config.strategies
        rcfg = ctx.config.risk
        st = ctx.state
        level = float(lv["price"])
        side = lv["side"]                       # 'sell' sweep -> long trap
        direction = "long" if side == "sell" else "short"

        # 1. sweep within the current session
        sweep = detect_sweep(df, level, side, lv["name"], start_ts=sess_start)
        if sweep is None:
            return None

        # 2. reclaim within config candles
        reclaim = detect_reclaim(df, sweep, rcfg.reclaim_max_candles)
        if reclaim is None:
            return None                          # price is trading through, not trapping

        # 3. structure confirmation after the sweep: MSS and/or CISD
        struct_dir = "bullish" if direction == "long" else "bearish"
        mss = detect_mss(df, swings, struct_dir, tf_s, after_ts=sweep.ts)
        cisd = detect_cisd(df, struct_dir, anchor_idx=sweep.idx,
                           max_candles_after=scfg.confirm_max_candles)
        events = [e for e in (mss, cisd) if e is not None]
        if not events:
            return None
        conf = min(events, key=lambda e: e.ts)   # earliest confirmation
        conf_bars_after_sweep = int((conf.ts - sweep.ts) // tf_s)
        if conf_bars_after_sweep > scfg.confirm_max_candles:
            return None                          # confirmation came too late — stale story

        conf_close_ts = conf.ts + tf_s

        # 4. entry: FVG born in the impulse (sweep bar -> confirmation bar), else structure retest
        entry_lo = entry_hi = entry_ref = None
        entry_kind = "structure_retest"
        impulse = df[(df["ts"] >= sweep.ts)].reset_index(drop=True)
        for g in detect_fvgs(impulse, tf_s):
            wanted = "bullish" if direction == "long" else "bearish"
            if g.kind != wanted or g.created_close_ts > ctx.horizon_ts:
                continue
            if g.created_bar_ts > conf.ts + scfg.confirm_max_candles * tf_s:
                continue
            if g.status(ctx.horizon_ts) in ("open", "mitigated"):
                entry_lo, entry_hi = g.bottom, g.top
                entry_ref = g.midpoint()
                entry_kind = "fvg_retest"
                break                            # first (earliest) qualifying gap
        if entry_ref is None:
            pad = scfg.entry_zone_atr_mult * cur_atr
            entry_ref = float(conf.level)
            entry_lo, entry_hi = entry_ref - pad, entry_ref + pad

        # 5. ATR-aware stop beyond the sweep extreme
        buf = scfg.stop_buffer_atr_mult * cur_atr
        stop = sweep.extreme - buf if direction == "long" else sweep.extreme + buf

        # 6. target = next liquidity level past the entry, in trade direction
        targets = _target_levels(ctx, direction)
        if direction == "long":
            reachable = sorted([(n, p) for n, p in targets if p > entry_hi + 1e-9], key=lambda t: t[1])
        else:
            reachable = sorted([(n, p) for n, p in targets if p < entry_lo - 1e-9], key=lambda t: -t[1])
        if not reachable:
            return None                          # nothing to reach for — no trade story
        t_name, t_price = reachable[0]
        tp2_name, tp2 = (reachable[1] if len(reachable) > 1 else (None, None))

        try:
            rr = rr_of(entry_ref, stop, t_price)
        except ValueError:
            return None                          # degenerate geometry

        # 7. context + grading
        confluences = [f"swept_{lv['name']}"]
        warnings: list[str] = []
        if reclaim.candles_after_sweep <= 2:
            confluences.append("fast_reclaim")
        if mss is not None:
            confluences.append("mss_confirmed")
        if cisd is not None:
            confluences.append("cisd_confirmed")
        if entry_kind == "fvg_retest":
            confluences.append("fvg_entry")

        pd_ctx = st.premium_discount_day
        if pd_ctx is not None:
            favorable = (direction == "long" and pd_ctx == "discount") or (
                direction == "short" and pd_ctx == "premium") or pd_ctx == "equilibrium"
            if favorable:
                confluences.append(f"{pd_ctx}_context")
            else:
                warnings.append(f"countertrend_range_position:{pd_ctx}")

        if st.vwap is not None:
            fav_vwap = (direction == "long" and st.vwap_position == "above") or (
                direction == "short" and st.vwap_position == "below")
            if fav_vwap:
                confluences.append("vwap_favorable")
            else:
                warnings.append(f"vwap_against:{st.vwap_position}")

        if rr >= scfg.min_target_rr_for_grade_a:
            confluences.append("rr_2_plus")

        score = len(confluences)
        grade = "A" if score >= 6 else ("B" if score >= 4 else "C")

        return SignalCandidate(
            strategy=self.name,
            setup_type=self.setup_type,
            symbol=ctx.symbol,
            direction=direction,
            ts=conf.ts,
            confirmed_close_ts=max(conf_close_ts, reclaim.ts + tf_s),
            detection_timeframe=tf,
            session=st.session,
            entry_lo=float(entry_lo), entry_hi=float(entry_hi), entry_ref=float(entry_ref),
            stop=float(stop),
            target=float(t_price), target_name=t_name,
            tp2=float(tp2) if tp2 is not None else None, tp2_name=tp2_name,
            rr=float(rr),
            grade=grade,
            confluences=confluences,
            warnings=warnings,
            context={
                "swept_level": lv["name"], "swept_price": level, "sweep_side": side,
                "sweep_ts": sweep.ts, "sweep_extreme": sweep.extreme,
                "reclaim_ts": reclaim.ts, "reclaim_candles": reclaim.candles_after_sweep,
                "mss_ts": mss.ts if mss else None, "mss_level": mss.level if mss else None,
                "cisd_ts": cisd.ts if cisd else None, "cisd_level": cisd.level if cisd else None,
                "confirmation_kind": conf.kind,
                "entry_kind": entry_kind,
                "atr": cur_atr,
                "premium_discount_day": st.premium_discount_day,
                "vwap": st.vwap, "vwap_position": st.vwap_position,
            },
        )

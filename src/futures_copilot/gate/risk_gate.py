"""Risk gate: SignalCandidate -> LONG / SHORT / WAIT / REJECT.

Decision semantics:
- no candidates                      -> WAIT (the default posture)
- candidate passes every hard check  -> LONG or SHORT (its direction)
- candidate fails any hard check     -> REJECT, with every failed reason listed

This module is the single writer of `signals.decision`. Nothing downstream
(packet layer, Claude, dashboard) can upgrade, downgrade, or edit a decision —
they can only display and annotate it. The checklist is emitted in full so a
human can audit exactly why a decision happened.

The news filter reads `risk.news_blackouts` from config.yaml — a MANUALLY
maintained list. No calendar scraping, no third-party feeds, by design.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import Config
from ..db.store import Store
from ..features.equal_levels import equal_level_near_stop
from ..features.market_state import MarketState
from ..features.sessions import trading_day as signal_trading_day
from ..strategies.base import SignalCandidate
from ..strategies.liquidity_trap import TF_SECONDS
from ..utils.roll_dates import is_in_roll_window

ET = ZoneInfo("America/New_York")

DECISIONS = ("LONG", "SHORT", "WAIT", "REJECT")
ROLL_WINDOW_REJECT_REASON = "Roll window active - contract rollover in progress"


@dataclass(frozen=True)
class CheckResult:
    check: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class GateDecision:
    """Final verdict for ONE candidate."""
    decision: str                        # LONG | SHORT | REJECT
    candidate: SignalCandidate
    checklist: list[CheckResult]
    reasons: list[str]                   # failed-check reasons (REJECT) — empty when passing
    warnings: list[str]
    invalidation: list[str]
    signal_id: int | None = None
    duplicate: bool = False


@dataclass
class GateOutput:
    """Verdict for a whole scan."""
    decision: str                        # LONG | SHORT | WAIT | REJECT
    chosen: GateDecision | None
    evaluations: list[GateDecision] = field(default_factory=list)

    def summary(self) -> str:
        if self.decision == "WAIT":
            return "WAIT — no qualifying candidates"
        parts = [f"{self.decision}"]
        if self.chosen is not None:
            c = self.chosen.candidate
            parts.append(f"{c.setup_type} {c.direction} entry~{c.entry_ref} stop={c.stop} "
                         f"target={c.target} rr={c.rr:.2f} grade={c.grade}")
        return " ".join(parts)


def _hm_minutes(s: str) -> int:
    h, _, m = s.partition(":")
    return int(h) * 60 + int(m)


def golden_hour_status(config: Config, horizon_ts: int) -> tuple[bool, str]:
    """(within_window, detail) for the DECISION time (market-state horizon).
    Start inclusive, end exclusive: 09:30 passes, 11:00 rejects. ET wall clock."""
    r = config.risk
    start_s, end_s = r.golden_hour[0], r.golden_hour[1]
    dt = datetime.fromtimestamp(horizon_ts, tz=ET)
    now_min = dt.hour * 60 + dt.minute
    within = _hm_minutes(start_s) <= now_min < _hm_minutes(end_s)
    return within, (f"decision time {dt:%H:%M} ET vs golden hour "
                    f"[{start_s}, {end_s}) — {'inside' if within else 'outside'}")


def trade_governor_status(store: Store, config: Config, symbol: str,
                          trading_day: str) -> tuple[bool, str, dict]:
    """(clear, detail, counts) from JOURNALED trade_reviews of this trading day.
    Wins: taken with result_r > 0. Losses: taken with result_r < 0.
    Skipped reviews and scratch/zero results count as neither."""
    r = config.risk
    results = store.day_trade_results(symbol, trading_day)
    wins = sum(1 for x in results if x["taken"] and (x["result_r"] or 0) > 0)
    losses = sum(1 for x in results if x["taken"] and (x["result_r"] or 0) < 0)
    blocked_win = r.stop_on_first_win and wins >= 1
    blocked_loss = losses >= r.stop_after_losses
    clear = not (blocked_win or blocked_loss)
    if blocked_win:
        detail = f"{wins} journaled win(s) today — stop_on_first_win is on; done for the day"
    elif blocked_loss:
        detail = f"{losses} journaled loss(es) today (max {r.stop_after_losses}) — done for the day"
    else:
        detail = (f"{wins} win(s), {losses} loss(es) journaled today "
                  f"(stop on first win: {r.stop_on_first_win}, stop after {r.stop_after_losses} losses)")
    return clear, detail, {"wins": wins, "losses": losses, "reviews": len(results)}


def _news_blackout(config: Config, horizon_ts: int) -> str | None:
    for nb in config.risk.news_blackouts:
        try:
            start = datetime.strptime(nb.start, "%Y-%m-%d %H:%M").replace(tzinfo=ET)
        except ValueError:
            return f"unparseable news_blackout start {nb.start!r} — fix config.yaml"
        s = int(start.timestamp())
        e = s + nb.minutes * 60
        if s <= horizon_ts < e:
            return nb.label or nb.start
    return None


def _roll_window_reject(cand: SignalCandidate) -> GateDecision:
    return GateDecision(
        decision="REJECT",
        candidate=cand,
        checklist=[
            CheckResult(
                "roll_window_clear",
                False,
                ROLL_WINDOW_REJECT_REASON,
            )
        ],
        reasons=[ROLL_WINDOW_REJECT_REASON],
        warnings=list(cand.warnings),
        invalidation=[],
    )


def _evaluate_candidate(
    cand: SignalCandidate, state: MarketState, store: Store, config: Config,
    passed_this_run: dict[str, int], counting_from_table: bool, history=None,
) -> GateDecision:
    # Roll gate — anchored to TRADING days (the 18:00 ET boundary), not raw
    # calendar dates, and checked at BOTH ends of the decision: the bar that
    # created the candidate AND the horizon the decision is made at. Every
    # date derives from a bar's UTC epoch through the ET-aware trading_day()
    # helper — no wall clock, no local timezone, no midnight/18:00-rollover
    # ambiguity, and no drift between the roll gate and the trading_day label
    # the signal is persisted (and displayed) under.
    cand_day = signal_trading_day(cand.ts, config.sessions)
    horizon_day = signal_trading_day(state.as_of_close_ts, config.sessions)
    if is_in_roll_window(cand_day) or is_in_roll_window(horizon_day):
        return _roll_window_reject(cand)

    r = config.risk
    horizon = state.as_of_close_ts
    tf_s = TF_SECONDS.get(cand.detection_timeframe, 300)
    atr = cand.context.get("atr") or state.atr_5m
    checks: list[CheckResult] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append(CheckResult(name, bool(ok), detail))

    # geometry present + sane (SignalCandidate validates, re-checked for audit trail)
    check("stop_and_target_required",
          cand.stop is not None and cand.target is not None and cand.rr > 0,
          f"stop={cand.stop} target={cand.target} rr={cand.rr:.2f}")

    # confirmed close only — the candidate must already be knowable
    check("confirmed_close_only", cand.confirmed_close_ts <= horizon,
          f"confirmed_close_ts={cand.confirmed_close_ts} horizon={horizon}")

    # minimum reward:risk
    check("min_rr", cand.rr >= r.min_rr, f"rr={cand.rr:.2f} min={r.min_rr}")

    # freshness: signal expires after N detection-TF candles
    bars_elapsed = max(0, (horizon - cand.confirmed_close_ts) // tf_s)
    check("not_stale", bars_elapsed <= r.signal_expiry_candles,
          f"{bars_elapsed} candles since confirmation (max {r.signal_expiry_candles})")

    # chased entry: price already ran past the entry zone
    if atr:
        price = state.current_price
        run_past = (price - cand.entry_hi) if cand.direction == "long" else (cand.entry_lo - price)
        max_run = r.max_entry_distance_atr_mult * atr
        check("entry_not_chased", run_past <= max_run,
              f"price {price} is {max(0.0, run_past):.2f} past the zone (max {max_run:.2f})")
    else:
        check("entry_not_chased", True, "no ATR available — cannot judge; see warnings")

    # stop width cap
    if atr:
        stop_dist = abs(cand.entry_ref - cand.stop)
        check("stop_width_within_atr_cap", stop_dist <= r.max_stop_atr_mult * atr,
              f"stop distance {stop_dist:.2f} vs cap {r.max_stop_atr_mult * atr:.2f}")
        # target must be meaningfully away
        tgt_dist = abs(cand.target - cand.entry_ref)
        check("target_not_too_close", tgt_dist >= r.min_target_atr_mult * atr,
              f"target distance {tgt_dist:.2f} vs min {r.min_target_atr_mult * atr:.2f}")
    else:
        check("stop_width_within_atr_cap", True, "no ATR — cannot judge")
        check("target_not_too_close", True, "no ATR — cannot judge")

    # session filter
    check("session_allowed", cand.session in r.allowed_sessions,
          f"session={cand.session} allowed={r.allowed_sessions}")

    # golden hour (Desk Mode): actionable only inside the ET window, judged at
    # the market-state horizon (as_of_close_ts) — same no-lookahead clock as
    # every other check. 09:30 inclusive, 11:00 exclusive.
    if r.enforce_golden_hour:
        within, gh_detail = golden_hour_status(config, horizon)
        check("golden_hour_allowed", within, gh_detail)
    else:
        check("golden_hour_allowed", True, "golden hour not enforced")

    # trade governor (Desk Mode): journaled outcomes end the day early.
    history = history or store
    gov_clear, gov_detail, _gov = trade_governor_status(
        history, config, cand.symbol, state.trading_day)
    check("trade_governor_clear", gov_clear, gov_detail)

    # equal high/low stop magnet (Desk Mode): stop must not sit inside an
    # obvious equal-lows/equal-highs cluster on the detection timeframe.
    if r.reject_equal_level_stop_magnets:
        spec = config.symbols.get(cand.symbol)
        if spec is None:
            check("stop_not_at_equal_liquidity", True,
                  f"no symbol spec for {cand.symbol} — cannot judge")
        else:
            tol = r.equal_level_tolerance_ticks * spec.tick_size
            df_det = store.get_candles_df(cand.symbol, cand.detection_timeframe)
            hit = equal_level_near_stop(
                df_det, direction=cand.direction, stop=cand.stop, tolerance=tol,
                lookback_bars=r.equal_level_lookback_bars,
                horizon_ts=horizon, tf_seconds=tf_s,
            )
            check("stop_not_at_equal_liquidity", hit is None,
                  hit.describe() if hit else
                  f"no equal-{'lows' if cand.direction == 'long' else 'highs'} cluster "
                  f"within {tol:.2f} of stop {cand.stop} "
                  f"(last {r.equal_level_lookback_bars} closed {cand.detection_timeframe} bars)")
    else:
        check("stop_not_at_equal_liquidity", True, "equal-level filter not enforced")

    # chop filter: developing day range must be worth trading
    day_span = None
    if state.session_levels:
        his = [lp.high for lp in state.session_levels.values() if lp is not None]
        los = [lp.low for lp in state.session_levels.values() if lp is not None]
        if his and los:
            day_span = max(his) - min(los)
    if day_span is not None and state.atr_15m:
        min_span = r.chop_min_day_range_atr_mult * state.atr_15m
        check("not_chop", day_span >= min_span,
              f"day span {day_span:.2f} vs min {min_span:.2f} ({r.chop_min_day_range_atr_mult}x ATR15)")
    else:
        check("not_chop", True, "insufficient data to judge chop — allowed with warning")

    # manual news blackout windows
    hit = _news_blackout(config, horizon)
    check("news_blackout_clear", hit is None, f"blackout: {hit}" if hit else "no blackout window active")

    # per-session / per-day caps. When this run persists signals, the table
    # already contains earlier passes from THIS run — adding passed_this_run
    # again would double-count. The in-run counter only matters when
    # persist=False (dry runs).
    sess_count = history.count_passing_signals(cand.symbol, state.trading_day, cand.session)
    day_count = history.count_passing_signals(cand.symbol, state.trading_day, None)
    if not counting_from_table:
        sess_count += passed_this_run.get(f"s:{cand.session}", 0)
        day_count += passed_this_run.get("day", 0)
    check("session_signal_cap", sess_count < r.max_signals_per_session,
          f"{sess_count} passing signals already this session (max {r.max_signals_per_session})")
    check("day_signal_cap", day_count < r.max_signals_per_day,
          f"{day_count} passing signals already today (max {r.max_signals_per_day})")

    failed = [c for c in checks if not c.passed]
    reasons = [f"{c.check}: {c.detail}" for c in failed]
    warnings = list(cand.warnings)
    for c in checks:
        if c.passed and "cannot judge" in c.detail:
            warnings.append(f"{c.check}: {c.detail}")

    if cand.direction == "long":
        invalidation = [f"5m close below stop {cand.stop}",
                        f"5m close back below swept level {cand.context.get('swept_price')}"]
    else:
        invalidation = [f"5m close above stop {cand.stop}",
                        f"5m close back above swept level {cand.context.get('swept_price')}"]

    decision = "REJECT" if failed else cand.direction.upper()
    return GateDecision(decision=decision, candidate=cand, checklist=checks,
                        reasons=reasons, warnings=warnings, invalidation=invalidation)


def evaluate(
    state: MarketState, candidates: list[SignalCandidate], store: Store, config: Config,
    persist: bool = True, history=None,
) -> GateOutput:
    """Gate a scan's candidates. Persists LONG/SHORT/REJECT signal rows
    (decision written here and ONLY here). WAIT is returned, not persisted."""
    if not candidates:
        return GateOutput(decision="WAIT", chosen=None, evaluations=[])

    ordered = sorted(candidates, key=lambda c: (c.grade, -c.rr))
    evaluations: list[GateDecision] = []
    passed_this_run: dict[str, int] = {}

    for cand in ordered:
        gd = _evaluate_candidate(cand, state, store, config, passed_this_run,
                                 counting_from_table=persist, history=history)
        if gd.decision in ("LONG", "SHORT"):
            passed_this_run[f"s:{cand.session}"] = passed_this_run.get(f"s:{cand.session}", 0) + 1
            passed_this_run["day"] = passed_this_run.get("day", 0) + 1
        if persist:
            dup = store.signal_exists(cand.symbol, cand.ts, cand.setup_type, cand.direction,
                                      swept_level=cand.context.get("swept_level"))
            if dup is not None:
                gd = GateDecision(**{**gd.__dict__, "signal_id": dup, "duplicate": True})
            else:
                sid = store.save_signal(
                    symbol=cand.symbol, ts=cand.ts, session=cand.session,
                    trading_day=state.trading_day, decision=gd.decision,
                    setup=cand.setup_type, grade=cand.grade,
                    entry_lo=cand.entry_lo, entry_hi=cand.entry_hi, stop=cand.stop,
                    tp1=cand.target, tp2=cand.tp2, rr=cand.rr,
                    reasons=json.dumps(gd.reasons), warnings=json.dumps(gd.warnings),
                    invalidation=json.dumps(gd.invalidation),
                    json_signal=json.dumps({
                        "candidate": cand.model_dump(),
                        "checklist": [c.__dict__ for c in gd.checklist],
                        "decision": gd.decision,
                    }, default=str),
                )
                gd = GateDecision(**{**gd.__dict__, "signal_id": sid})
        evaluations.append(gd)

    passing = [g for g in evaluations if g.decision in ("LONG", "SHORT")]
    if passing:
        chosen = passing[0]
        return GateOutput(decision=chosen.decision, chosen=chosen, evaluations=evaluations)
    return GateOutput(decision="REJECT", chosen=None, evaluations=evaluations)

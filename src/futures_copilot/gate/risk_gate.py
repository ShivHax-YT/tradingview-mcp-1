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
from ..features.market_state import MarketState
from ..strategies.base import SignalCandidate
from ..strategies.liquidity_trap import TF_SECONDS

ET = ZoneInfo("America/New_York")

DECISIONS = ("LONG", "SHORT", "WAIT", "REJECT")


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


def _evaluate_candidate(
    cand: SignalCandidate, state: MarketState, store: Store, config: Config,
    passed_this_run: dict[str, int],
) -> GateDecision:
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

    # per-session / per-day caps (counts persisted passing signals + earlier passes this run)
    sess_count = store.count_passing_signals(cand.symbol, state.trading_day, cand.session)
    sess_count += passed_this_run.get(f"s:{cand.session}", 0)
    check("session_signal_cap", sess_count < r.max_signals_per_session,
          f"{sess_count} passing signals already this session (max {r.max_signals_per_session})")
    day_count = store.count_passing_signals(cand.symbol, state.trading_day, None)
    day_count += passed_this_run.get("day", 0)
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
    persist: bool = True,
) -> GateOutput:
    """Gate a scan's candidates. Persists LONG/SHORT/REJECT signal rows
    (decision written here and ONLY here). WAIT is returned, not persisted."""
    if not candidates:
        return GateOutput(decision="WAIT", chosen=None, evaluations=[])

    ordered = sorted(candidates, key=lambda c: (c.grade, -c.rr))
    evaluations: list[GateDecision] = []
    passed_this_run: dict[str, int] = {}

    for cand in ordered:
        gd = _evaluate_candidate(cand, state, store, config, passed_this_run)
        if gd.decision in ("LONG", "SHORT"):
            passed_this_run[f"s:{cand.session}"] = passed_this_run.get(f"s:{cand.session}", 0) + 1
            passed_this_run["day"] = passed_this_run.get("day", 0) + 1
        if persist:
            dup = store.signal_exists(cand.symbol, cand.ts, cand.setup_type, cand.direction)
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

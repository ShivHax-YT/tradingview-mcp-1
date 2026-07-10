"""Offline Golden Hour replay using production strategy and risk rules.

Every scan and gate call is explicitly ``persist=False``. New decisions become
fillable on the next 1-minute bar, and all fill outcomes use adverse,
symbol-specific slippage. A stop wins every same-bar stop/target tie.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from math import inf
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ..config import Config, SlippageConfig
from ..data.resample import TF_SECONDS
from ..db.store import Store
from ..gate import evaluate
from ..strategies import scan
from ..strategies.base import SignalCandidate

ET = ZoneInfo("America/New_York")


@dataclass
class BacktestTrade:
    key: str
    symbol: str
    trading_day: str
    setup: str
    direction: str
    grade: str
    decision_ts: int
    eligible_ts: int
    entry_lo: float
    entry_hi: float
    entry_ref: float
    stop: float
    target: float
    status: str = "pending"  # pending | open | target | stop | unfilled
    activated_ts: int | None = None
    exit_ts: int | None = None
    fill_price: float | None = None
    exit_price: float | None = None
    result_r: float | None = None
    tie_break_stop: bool = False
    expires_ts: int = 0


@dataclass
class BacktestReport:
    symbol: str
    start_ts: int
    end_ts: int
    bars_processed: int
    trades: list[BacktestTrade]
    filter_metrics: dict[str, dict[str, int]]
    rejection_reasons: dict[str, int]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "bars_processed": self.bars_processed,
            "trades": [asdict(t) for t in self.trades],
            "filter_metrics": self.filter_metrics,
            "rejection_reasons": self.rejection_reasons,
            "metrics": self.metrics,
        }


@dataclass
class ReplayLedger:
    """Only events produced by this replay; never reads the live journal."""

    passing: list[tuple[str, str, str | None]] = field(default_factory=list)
    results: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)

    def count_passing_signals(self, symbol: str, trading_day: str, session: str | None) -> int:
        return sum(
            1 for sym, day, sess in self.passing
            if sym == symbol and day == trading_day and (session is None or sess == session)
        )

    def day_trade_results(self, symbol: str, trading_day: str) -> list[dict[str, Any]]:
        return list(self.results.get((symbol, trading_day), []))

    def record_signal(self, symbol: str, trading_day: str, session: str | None) -> None:
        self.passing.append((symbol, trading_day, session))

    def record_result(self, symbol: str, trading_day: str, result_r: float) -> None:
        self.results.setdefault((symbol, trading_day), []).append(
            {"taken": True, "result_r": result_r}
        )


def _candidate_key(candidate: SignalCandidate) -> str:
    swept = candidate.context.get("swept_level", "")
    return ":".join(map(str, (
        candidate.symbol,
        candidate.setup_type,
        candidate.direction,
        candidate.ts,
        swept,
    )))


def _new_trade(
    candidate: SignalCandidate,
    trading_day: str,
    decision_ts: int,
    config: Config,
) -> BacktestTrade:
    tf_seconds = TF_SECONDS.get(candidate.detection_timeframe, 300)
    return BacktestTrade(
        key=_candidate_key(candidate),
        symbol=candidate.symbol,
        trading_day=trading_day,
        setup=candidate.setup_type,
        direction=candidate.direction,
        grade=candidate.grade,
        decision_ts=decision_ts,
        eligible_ts=decision_ts,
        entry_lo=candidate.entry_lo,
        entry_hi=candidate.entry_hi,
        entry_ref=candidate.entry_ref,
        stop=candidate.stop,
        target=candidate.target,
        expires_ts=(
            candidate.confirmed_close_ts
            + config.risk.signal_expiry_candles * tf_seconds
        ),
    )


def _touches_zone(trade: BacktestTrade, bar: Any) -> bool:
    return float(bar.high) >= trade.entry_lo and float(bar.low) <= trade.entry_hi


def _advance_trade(
    trade: BacktestTrade,
    bar: Any,
    *,
    tick_size: float,
    slippage: SlippageConfig,
) -> bool:
    """Advance one order by one bar; return True only when it closes."""
    bar_ts = int(bar.ts)
    activated_now = False
    if trade.status == "pending":
        if bar_ts < trade.eligible_ts:
            return False
        if bar_ts > trade.expires_ts:
            trade.status = "unfilled"
            return False
        if not _touches_zone(trade, bar):
            return False
        trade.status = "open"
        activated_now = True
        trade.activated_ts = bar_ts
        if trade.direction == "long":
            trade.fill_price = (
                trade.entry_hi + slippage.entry_ticks * tick_size
                if float(bar.high) >= trade.entry_hi
                else float(bar.high)
            )
        else:
            trade.fill_price = (
                trade.entry_lo - slippage.entry_ticks * tick_size
                if float(bar.low) <= trade.entry_lo
                else float(bar.low)
            )

    if trade.status != "open":
        return False

    stop_hit = (
        float(bar.low) <= trade.stop if trade.direction == "long"
        else float(bar.high) >= trade.stop
    )
    raw_target_hit = (
        float(bar.high) >= trade.target if trade.direction == "long"
        else float(bar.low) <= trade.target
    )
    target_hit = not activated_now and raw_target_hit
    if not stop_hit and not target_hit:
        return False

    # Absolute conservative rule: stop first whenever both fit inside one bar.
    stopped = stop_hit
    trade.tie_break_stop = stop_hit and raw_target_hit
    trade.status = "stop" if stopped else "target"
    trade.exit_ts = bar_ts
    if trade.direction == "long":
        if stopped:
            stop_base = float(bar.open) if float(bar.open) < trade.stop else trade.stop
            trade.exit_price = stop_base - slippage.stop_ticks * tick_size
        elif float(bar.open) > trade.target:
            trade.exit_price = float(bar.open)
        else:
            trade.exit_price = trade.target - slippage.target_ticks * tick_size
        pnl = trade.exit_price - float(trade.fill_price)
    else:
        if stopped:
            stop_base = float(bar.open) if float(bar.open) > trade.stop else trade.stop
            trade.exit_price = stop_base + slippage.stop_ticks * tick_size
        elif float(bar.open) < trade.target:
            trade.exit_price = float(bar.open)
        else:
            trade.exit_price = trade.target + slippage.target_ticks * tick_size
        pnl = float(trade.fill_price) - trade.exit_price
    planned_risk = abs(trade.entry_ref - trade.stop)
    trade.result_r = round(pnl / planned_risk, 6)
    return True


def _profit_factor(values: list[float]) -> float | None:
    gains = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses == 0:
        return inf if gains > 0 else None
    return gains / losses


def _metrics(trades: list[BacktestTrade]) -> dict[str, Any]:
    closed = [t for t in trades if t.status in ("target", "stop")]
    values = [float(t.result_r) for t in closed if t.result_r is not None]
    wins = sum(v > 0 for v in values)
    losses = sum(v < 0 for v in values)
    by_grade: dict[str, dict[str, Any]] = {}
    for grade in ("A", "B", "C"):
        gv = [float(t.result_r) for t in closed if t.grade == grade and t.result_r is not None]
        gw = sum(v > 0 for v in gv)
        gl = sum(v < 0 for v in gv)
        by_grade[grade] = {
            "trades": len(gv),
            "wins": gw,
            "losses": gl,
            "win_rate": (gw / (gw + gl)) if gw + gl else None,
            "expectancy_r": (sum(gv) / len(gv)) if gv else None,
            "profit_factor": _profit_factor(gv),
        }
    return {
        "accepted": len(trades),
        "closed": len(closed),
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / (wins + losses)) if wins + losses else None,
        "expectancy_r": (sum(values) / len(values)) if values else None,
        "profit_factor": _profit_factor(values),
        "unfilled": sum(t.status == "unfilled" for t in trades),
        "open_at_end": sum(t.status == "open" for t in trades),
        "by_grade": by_grade,
    }


def run_backtest(
    store: Store,
    config: Config,
    symbol: str,
    start_ts: int,
    end_ts: int,
    *,
    _scanner: Callable[..., Any] = scan,
    _gate: Callable[..., Any] = evaluate,
) -> BacktestReport:
    """Replay stored 1m bars over ``[start_ts, end_ts)`` without DB writes."""
    if end_ts <= start_ts:
        raise ValueError("backtest --end must be later than --start")
    bars = store.get_candles_df(symbol, "1m", start_ts=start_ts, end_ts=end_ts - 1)
    if bars.empty:
        raise ValueError(f"no stored 1m bars for {symbol} in the requested range")
    if symbol not in config.symbols:
        raise ValueError(f"symbol {symbol!r} is not configured")
    slippage = config.backtest.slippage.get(symbol, SlippageConfig())
    tick_size = config.symbols[symbol].tick_size
    ledger = ReplayLedger()
    trades: list[BacktestTrade] = []
    consumed: set[str] = set()
    filter_counts: dict[str, Counter] = defaultdict(Counter)
    reasons: Counter = Counter()

    for bar in bars.itertuples(index=False):
        for trade in trades:
            if trade.status not in ("pending", "open"):
                continue
            if _advance_trade(trade, bar, tick_size=tick_size, slippage=slippage):
                ledger.record_result(trade.symbol, trade.trading_day, float(trade.result_r))

        # as_of_ts is the 1m bar OPEN; build_market_state derives the close
        # horizon. Both production seams are explicitly non-persistent.
        scan_result = _scanner(
            store, config, symbol, as_of_ts=int(bar.ts), persist=False,
        )
        fresh = [c for c in scan_result.candidates if _candidate_key(c) not in consumed]
        if not fresh:
            continue
        gate = _gate(
            scan_result.state,
            fresh,
            store,
            config,
            persist=False,
            history=ledger,
            now_ts=scan_result.state.as_of_close_ts,
        )
        for decision in gate.evaluations:
            for check in decision.checklist:
                bucket = filter_counts[check.check]
                bucket["evaluated"] += 1
                bucket["passed" if check.passed else "failed"] += 1
            reasons.update(decision.reasons)
            if decision.decision not in ("LONG", "SHORT"):
                continue
            candidate = decision.candidate
            consumed.add(_candidate_key(candidate))
            ledger.record_signal(candidate.symbol, scan_result.state.trading_day, candidate.session)
            trades.append(_new_trade(
                candidate, scan_result.state.trading_day,
                scan_result.state.as_of_close_ts, config,
            ))

    for trade in trades:
        if trade.status == "pending":
            trade.status = "unfilled"

    return BacktestReport(
        symbol=symbol,
        start_ts=start_ts,
        end_ts=end_ts,
        bars_processed=len(bars),
        trades=trades,
        filter_metrics={k: dict(v) for k, v in sorted(filter_counts.items())},
        rejection_reasons=dict(sorted(reasons.items())),
        metrics=_metrics(trades),
    )


def _fmt_ratio(value: float | None, *, pct: bool = False) -> str:
    if value is None:
        return "n/a"
    if value == inf:
        return "∞"
    return f"{value * 100:.1f}%" if pct else f"{value:.3f}"


def render_markdown(report: BacktestReport) -> str:
    metrics = report.metrics
    start = datetime.fromtimestamp(report.start_ts, tz=ET)
    end = datetime.fromtimestamp(report.end_ts, tz=ET)
    lines = [
        f"# Golden Hour Backtest — {report.symbol}",
        "",
        f"Range: {start:%Y-%m-%d %H:%M ET} to {end:%Y-%m-%d %H:%M ET} (end exclusive)",
        f"Bars replayed: {report.bars_processed} · accepted: {metrics['accepted']} · closed: {metrics['closed']}",
        "",
        "## Performance Summary",
        "",
        "| expectancy (R) | profit factor | win rate | wins | losses | unfilled | open at end |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        f"| {_fmt_ratio(metrics['expectancy_r'])} | {_fmt_ratio(metrics['profit_factor'])} | "
        f"{_fmt_ratio(metrics['win_rate'], pct=True)} | {metrics['wins']} | {metrics['losses']} | "
        f"{metrics['unfilled']} | {metrics['open_at_end']} |",
        "",
        "## Results by Grade",
        "",
        "| grade | trades | wins | losses | win rate | expectancy R | profit factor |",
        "|:---:|---:|---:|---:|---:|---:|---:|",
    ]
    for grade, gm in metrics["by_grade"].items():
        lines.append(
            f"| {grade} | {gm['trades']} | {gm['wins']} | {gm['losses']} | "
            f"{_fmt_ratio(gm['win_rate'], pct=True)} | {_fmt_ratio(gm['expectancy_r'])} | "
            f"{_fmt_ratio(gm['profit_factor'])} |"
        )
    lines += [
        "",
        "## Trade Governor and Risk Filter Interactions",
        "",
        "| filter | evaluated | passed | failed |",
        "|---|---:|---:|---:|",
    ]
    for name, counts in report.filter_metrics.items():
        lines.append(
            f"| {name} | {counts.get('evaluated', 0)} | "
            f"{counts.get('passed', 0)} | {counts.get('failed', 0)} |"
        )
    if report.rejection_reasons:
        lines += ["", "### Rejection reasons", ""]
        lines.extend(f"- {reason}: {count}" for reason, count in report.rejection_reasons.items())
    lines += [
        "",
        "_Offline deterministic replay. Strategy scans and risk gates ran with persist=False; "
        "same-bar stop/target ties resolve to the stop._",
        "",
    ]
    return "\n".join(lines)


def parse_cli_range(start: str, end: str) -> tuple[int, int]:
    """Parse ET dates/ISO datetimes; a date-only end includes that whole day."""

    def parse_one(value: str, *, end_boundary: bool) -> datetime:
        try:
            if "T" not in value and " " not in value:
                d = date.fromisoformat(value)
                if end_boundary:
                    d += timedelta(days=1)
                return datetime.combine(d, time.min, tzinfo=ET)
            dt = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"invalid backtest boundary {value!r}; use YYYY-MM-DD or ISO datetime") from exc
        return dt.replace(tzinfo=ET) if dt.tzinfo is None else dt

    start_dt = parse_one(start, end_boundary=False)
    end_dt = parse_one(end, end_boundary=True)
    start_ts, end_ts = int(start_dt.timestamp()), int(end_dt.timestamp())
    if end_ts <= start_ts:
        raise ValueError("backtest --end must be later than --start")
    return start_ts, end_ts

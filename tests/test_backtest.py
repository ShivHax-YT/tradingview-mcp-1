from __future__ import annotations

from types import SimpleNamespace

import pytest

from futures_copilot.backtest.engine import (
    BacktestTrade,
    _advance_trade,
    _metrics,
    _new_trade,
    parse_cli_range,
    render_markdown,
    run_backtest,
)
from futures_copilot.gate.risk_gate import CheckResult, GateDecision, GateOutput
from futures_copilot.models import Candle
from futures_copilot.strategies.base import SignalCandidate

from .feature_helpers import et_ts
from .strategy_fixtures import seed_long_trap


def _candidate(ts: int, *, direction: str = "long", grade: str = "A") -> SignalCandidate:
    if direction == "long":
        lo, hi, ref, stop, target = 99.75, 100.0, 100.0, 99.0, 102.0
    else:
        lo, hi, ref, stop, target = 100.0, 100.25, 100.0, 101.0, 98.0
    return SignalCandidate(
        strategy="fixture", setup_type="fixture", symbol="MNQ", direction=direction,
        ts=ts, confirmed_close_ts=ts + 60, detection_timeframe="1m", session="ny",
        entry_lo=lo, entry_hi=hi, entry_ref=ref, stop=stop, target=target,
        target_name="fixture", rr=2.0, grade=grade,
    )


def _bars(store, start: int):
    rows = [
        (100, 100.1, 99.9, 100),
        (100, 103.0, 98.0, 100),  # entry + stop + target: stop must win
        (100, 103.0, 99.5, 102),
    ]
    store.upsert_candles([
        Candle(symbol="MNQ", timeframe="1m", ts=start + i * 60,
               open=o, high=h, low=l, close=c, volume=1, source="fixture")
        for i, (o, h, l, c) in enumerate(rows)
    ])


def test_replay_calls_scan_and_gate_with_persist_false_and_tie_stops(config, store):
    start = et_ts(2026, 6, 24, 9, 30)
    _bars(store, start)
    calls = []
    candidate = _candidate(start)

    def scanner(_store, _config, _symbol, *, as_of_ts, persist):
        calls.append(("scan", as_of_ts, persist))
        return SimpleNamespace(
            state=SimpleNamespace(trading_day="2026-06-24", as_of_close_ts=as_of_ts + 60),
            candidates=[candidate],
        )

    def gate(state, candidates, _store, _config, *, persist, history, now_ts):
        assert now_ts == state.as_of_close_ts
        calls.append(("gate", state.as_of_close_ts, persist, history is not None))
        decisions = [GateDecision(
            decision="LONG", candidate=c, checklist=[CheckResult("golden_hour_allowed", True, "ok")],
            reasons=[], warnings=[], invalidation=[],
        ) for c in candidates]
        return GateOutput("LONG", decisions[0] if decisions else None, decisions)

    before = {table: store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in ("market_states", "strategy_outputs", "signals", "trade_reviews")}
    report = run_backtest(
        store, config, "MNQ", start, start + 3 * 60, _scanner=scanner, _gate=gate,
    )
    after = {table: store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
             for table in before}

    assert before == after
    assert all(call[2] is False for call in calls)
    assert len(report.trades) == 1
    trade = report.trades[0]
    assert trade.status == "stop" and trade.tie_break_stop is True
    assert trade.activated_ts == start + 60  # never fills on its decision bar
    assert trade.fill_price == 100.25 and trade.exit_price == 98.5
    assert trade.result_r == -1.75


def test_replay_is_exactly_deterministic(config, store):
    start = et_ts(2026, 6, 24, 9, 30)
    _bars(store, start)
    candidate = _candidate(start)

    def scanner(_store, _config, _symbol, *, as_of_ts, persist):
        return SimpleNamespace(
            state=SimpleNamespace(trading_day="2026-06-24", as_of_close_ts=as_of_ts + 60),
            candidates=[candidate],
        )

    def gate(state, candidates, _store, _config, *, persist, history, now_ts):
        decisions = [GateDecision("LONG", c, [], [], [], []) for c in candidates]
        return GateOutput("LONG" if decisions else "WAIT", decisions[0] if decisions else None, decisions)

    first = run_backtest(store, config, "MNQ", start, start + 180,
                         _scanner=scanner, _gate=gate).to_dict()
    second = run_backtest(store, config, "MNQ", start, start + 180,
                          _scanner=scanner, _gate=gate).to_dict()
    assert first == second


def test_session_filter_excludes_out_of_session_entries(config, store):
    start = et_ts(2026, 6, 24, 9, 30)
    _bars(store, start)
    candidate = _candidate(start)

    def scanner(_store, _config, _symbol, *, as_of_ts, persist):
        return SimpleNamespace(
            state=SimpleNamespace(trading_day="2026-06-24", as_of_close_ts=as_of_ts + 60),
            candidates=[candidate],
        )

    def gate(state, candidates, _store, _config, *, persist, history, now_ts):
        decisions = [GateDecision("LONG", c, [], [], [], []) for c in candidates]
        return GateOutput("LONG" if decisions else "WAIT", decisions[0] if decisions else None, decisions)

    ny = run_backtest(
        store, config, "MNQ", start, start + 180, session="ny",
        _scanner=scanner, _gate=gate,
    )
    asia = run_backtest(
        store, config, "MNQ", start, start + 180, session="asia",
        _scanner=scanner, _gate=gate,
    )
    assert len(ny.trades) == 1 and asia.trades == []
    assert ny.metrics["by_session"]["ny"]["accepted"] == 1
    assert asia.session_filter == "asia"


def test_transient_rejection_is_reevaluated_until_candidate_passes(config, store):
    start = et_ts(2026, 6, 24, 9, 30)
    _bars(store, start)
    candidate = _candidate(start)
    gate_calls = 0

    def scanner(_store, _config, _symbol, *, as_of_ts, persist):
        return SimpleNamespace(
            state=SimpleNamespace(trading_day="2026-06-24", as_of_close_ts=as_of_ts + 60),
            candidates=[candidate],
        )

    def gate(state, candidates, _store, _config, *, persist, history, now_ts):
        nonlocal gate_calls
        gate_calls += 1
        decision = "REJECT" if gate_calls == 1 else "LONG"
        evaluations = [GateDecision(
            decision, c, [], ["temporary"] if decision == "REJECT" else [], [], [],
        ) for c in candidates]
        return GateOutput(decision, evaluations[0] if decision == "LONG" else None, evaluations)

    report = run_backtest(
        store, config, "MNQ", start, start + 180, _scanner=scanner, _gate=gate,
    )

    assert gate_calls == 2
    assert len(report.trades) == 1


def test_same_bar_tie_rule_is_symmetric_for_short(config):
    trade = BacktestTrade(
        key="short", symbol="MNQ", trading_day="2026-06-24", setup="fixture",
        direction="short", grade="B", decision_ts=1, eligible_ts=1,
        entry_lo=100, entry_hi=100.25, entry_ref=100, stop=101, target=98,
        expires_ts=10,
    )
    bar = SimpleNamespace(ts=1, open=100, high=102, low=97, close=100)

    closed = _advance_trade(
        trade, bar, tick_size=config.symbols["MNQ"].tick_size,
        slippage=config.backtest.slippage["MNQ"],
    )

    assert closed is True and trade.status == "stop" and trade.tie_break_stop is True
    assert trade.fill_price == 99.75 and trade.exit_price == 101.5
    assert trade.result_r == -1.75


def test_activation_bar_cannot_award_unprovable_target(config):
    trade = BacktestTrade(
        key="activation-target", symbol="MNQ", trading_day="2026-06-24", setup="fixture",
        direction="long", grade="A", decision_ts=1, eligible_ts=1,
        entry_lo=99.75, entry_hi=100, entry_ref=100, stop=99, target=102,
        expires_ts=10,
    )
    activation = SimpleNamespace(ts=1, open=102, high=102.5, low=99.75, close=100.5)

    assert _advance_trade(
        trade, activation, tick_size=config.symbols["MNQ"].tick_size,
        slippage=config.backtest.slippage["MNQ"],
    ) is False
    assert trade.status == "open" and trade.exit_ts is None

    later = SimpleNamespace(ts=2, open=100.5, high=102.5, low=100, close=102)
    assert _advance_trade(
        trade, later, tick_size=config.symbols["MNQ"].tick_size,
        slippage=config.backtest.slippage["MNQ"],
    ) is True
    assert trade.status == "target"


def test_partial_zone_touch_fill_stays_inside_bar(config):
    trade = BacktestTrade(
        key="partial", symbol="MNQ", trading_day="2026-06-24", setup="fixture",
        direction="long", grade="A", decision_ts=1, eligible_ts=1,
        entry_lo=99.75, entry_hi=100, entry_ref=100, stop=99, target=102,
        expires_ts=10,
    )
    bar = SimpleNamespace(ts=1, open=99.76, high=99.80, low=99.74, close=99.78)

    _advance_trade(
        trade, bar, tick_size=config.symbols["MNQ"].tick_size,
        slippage=config.backtest.slippage["MNQ"],
    )

    assert bar.low <= trade.fill_price <= bar.high
    assert trade.fill_price == 99.80


@pytest.mark.parametrize(
    ("direction", "bar", "expected"),
    [
        ("long", SimpleNamespace(ts=1, open=99.75, high=100.5, low=99.5, close=100), 100.25),
        ("short", SimpleNamespace(ts=1, open=100.25, high=100.5, low=99.5, close=100), 99.75),
    ],
)
def test_entry_fill_uses_adverse_zone_edge_plus_slippage(config, direction, bar, expected):
    candidate = _candidate(0, direction=direction)
    trade = _new_trade(candidate, "2026-06-24", 0, config)
    trade.eligible_ts = 1

    assert _advance_trade(
        trade, bar, tick_size=config.symbols["MNQ"].tick_size,
        slippage=config.backtest.slippage["MNQ"],
    ) is False

    assert trade.status == "open"
    assert trade.fill_price == expected


@pytest.mark.parametrize(
    ("direction", "open_price", "high", "low", "expected"),
    [
        ("long", 98.0, 99.5, 97.5, 97.5),
        ("short", 102.0, 102.5, 100.5, 102.5),
    ],
)
def test_stop_gap_fills_at_open_with_adverse_slippage(
    config, direction, open_price, high, low, expected,
):
    candidate = _candidate(0, direction=direction)
    trade = _new_trade(candidate, "2026-06-24", 0, config)
    trade.status = "open"
    trade.fill_price = candidate.entry_ref
    bar = SimpleNamespace(ts=2, open=open_price, high=high, low=low, close=open_price)

    assert _advance_trade(
        trade, bar, tick_size=config.symbols["MNQ"].tick_size,
        slippage=config.backtest.slippage["MNQ"],
    ) is True

    assert trade.status == "stop"
    assert trade.exit_price == expected


@pytest.mark.parametrize(
    ("direction", "bar", "expected"),
    [
        ("long", SimpleNamespace(ts=2, open=100, high=100.5, low=98.75, close=99), 98.5),
        ("short", SimpleNamespace(ts=2, open=100, high=101.25, low=99.5, close=101), 101.5),
    ],
)
def test_normal_stop_touch_fills_at_stop_with_adverse_slippage(config, direction, bar, expected):
    candidate = _candidate(0, direction=direction)
    trade = _new_trade(candidate, "2026-06-24", 0, config)
    trade.status = "open"
    trade.fill_price = candidate.entry_ref

    assert _advance_trade(
        trade, bar, tick_size=config.symbols["MNQ"].tick_size,
        slippage=config.backtest.slippage["MNQ"],
    ) is True

    assert trade.status == "stop"
    assert trade.exit_price == expected


@pytest.mark.parametrize(
    ("direction", "open_price", "high", "low"),
    [
        ("long", 103.0, 103.5, 102.5),
        ("short", 97.0, 97.5, 96.5),
    ],
)
def test_target_gap_fills_at_favorable_open_when_provable(
    config, direction, open_price, high, low,
):
    candidate = _candidate(0, direction=direction)
    trade = _new_trade(candidate, "2026-06-24", 0, config)
    trade.status = "open"
    trade.fill_price = candidate.entry_ref
    bar = SimpleNamespace(ts=2, open=open_price, high=high, low=low, close=open_price)

    assert _advance_trade(
        trade, bar, tick_size=config.symbols["MNQ"].tick_size,
        slippage=config.backtest.slippage["MNQ"],
    ) is True

    assert trade.status == "target"
    assert trade.exit_price == open_price


def test_order_expiry_is_anchored_to_candidate_confirmation(config):
    candidate = _candidate(1_750_000_000)
    decision_ts = candidate.confirmed_close_ts + 120

    trade = _new_trade(candidate, "2026-06-24", decision_ts, config)

    assert trade.expires_ts == (
        candidate.confirmed_close_ts
        + config.risk.signal_expiry_candles * 60
    )
    assert trade.expires_ts < decision_ts + config.risk.signal_expiry_candles * 60


def test_metrics_include_expectancy_profit_factor_and_grade_breakdown():
    trades = []
    for grade, result in (("A", 2.0), ("A", -1.0), ("B", 1.0)):
        trade = BacktestTrade(
            key=f"{grade}{result}", symbol="MNQ", trading_day="x", setup="x",
            direction="long", grade=grade, decision_ts=1, eligible_ts=1,
            entry_lo=1, entry_hi=1, entry_ref=1, stop=0, target=2,
            status="target" if result > 0 else "stop", result_r=result,
        )
        trades.append(trade)

    metrics = _metrics(trades)

    assert metrics["expectancy_r"] == pytest.approx(2 / 3)
    assert metrics["profit_factor"] == 3.0
    assert metrics["win_rate"] == pytest.approx(2 / 3)
    assert metrics["by_grade"]["A"]["profit_factor"] == 2.0
    assert metrics["by_grade"]["B"]["win_rate"] == 1.0


def test_markdown_reports_filters_and_governor(config, store):
    start = et_ts(2026, 6, 24, 9, 30)
    _bars(store, start)
    candidate = _candidate(start)

    def scanner(_store, _config, _symbol, *, as_of_ts, persist):
        return SimpleNamespace(
            state=SimpleNamespace(trading_day="2026-06-24", as_of_close_ts=as_of_ts + 60),
            candidates=[candidate],
        )

    def gate(state, candidates, _store, _config, *, persist, history, now_ts):
        decisions = [GateDecision(
            "LONG", c, [CheckResult("trade_governor_clear", True, "clear")], [], [], [],
        ) for c in candidates]
        return GateOutput("LONG" if decisions else "WAIT", decisions[0] if decisions else None, decisions)

    md = render_markdown(run_backtest(
        store, config, "MNQ", start, start + 180, _scanner=scanner, _gate=gate,
    ))

    assert "expectancy (R)" in md
    assert "Results by Grade" in md
    assert "trade_governor_clear" in md
    assert "persist=False" in md


def test_cli_range_date_end_is_inclusive_day():
    start, end = parse_cli_range("2026-06-24", "2026-06-24")
    assert end - start == 24 * 60 * 60


def test_backtest_requires_stored_1m_bars(config, store):
    with pytest.raises(ValueError, match="no stored 1m bars"):
        run_backtest(store, config, "MNQ", 1_800_000_000, 1_800_000_060)


def test_real_strategy_gate_replay_is_deterministic_and_write_isolated(config, store):
    seed_long_trap(store)
    start = et_ts(2026, 6, 24, 9, 30)
    end = et_ts(2026, 6, 24, 10, 21)
    before = {table: store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in ("market_states", "strategy_outputs", "signals", "trade_reviews")}

    first = run_backtest(store, config, "MNQ", start, end).to_dict()
    second = run_backtest(store, config, "MNQ", start, end).to_dict()
    after = {table: store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
             for table in before}

    assert first == second
    assert before == after
    assert first["bars_processed"] == 51
    assert "golden_hour_allowed" in first["filter_metrics"]


def test_real_replay_ignores_future_bars_and_live_journal(config, store):
    seed_long_trap(store)
    start = et_ts(2026, 6, 24, 9, 30)
    end = et_ts(2026, 6, 24, 10, 11)
    baseline = run_backtest(store, config, "MNQ", start, end).to_dict()
    # Future candle and future live-journal win must not alter an earlier replay.
    store.upsert_candles([Candle(
        symbol="MNQ", timeframe="1m", ts=end + 3600,
        open=100, high=99999, low=1, close=100, volume=1, source="fixture",
    )])
    sid = store.save_signal(
        symbol="MNQ", ts=end + 3600, session="ny", trading_day="2026-06-24",
        decision="LONG", setup="future", grade="A", entry_lo=100, entry_hi=101,
        stop=99, tp1=103, tp2=None, rr=2, reasons="[]", warnings="[]",
        invalidation="[]", json_signal='{"candidate":{"direction":"long"}}',
    )
    store.add_trade_review(signal_id=sid, symbol="MNQ", taken=True, result_r=2.0)

    replayed = run_backtest(store, config, "MNQ", start, end).to_dict()

    assert replayed == baseline


def test_cli_backtest_uses_read_only_database(config, store, tmp_path, capsys):
    from futures_copilot.cli import main

    seed_long_trap(store)
    cfg_text = (config.root / "config.yaml").read_text(encoding="utf-8")
    cfg_text = cfg_text.replace(
        "db_path: data/copilot.db",
        f'db_path: "{store.db_path.as_posix()}"',
    )
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    before = store.conn.execute("SELECT COUNT(*) FROM trade_reviews").fetchone()[0]

    rc = main([
        "--config", str(cfg_path), "backtest", "--symbol", "MNQ",
        "--start", "2026-06-24T09:30:00", "--end", "2026-06-24T09:35:00",
    ])

    assert rc == 0
    assert "Golden Hour Backtest" in capsys.readouterr().out
    assert store.conn.execute("SELECT COUNT(*) FROM trade_reviews").fetchone()[0] == before

"""Collector tests run against the fixture source (offline). The tvmcp source is
exercised for real only via `copilot health` / `copilot backfill` on the user's
machine with TradingView Desktop running — see tests/test_tvmcp_unit.py for the
pure-logic parts that ARE testable offline."""

from futures_copilot.data.collector import backfill, collect_once, status
from futures_copilot.data.fixtures import FixtureCandleSource


def test_backfill_writes_native_and_derived(config, store, fixture_csv):
    # Restrict to what the fixture actually contains: MNQ 1m.
    config.data.backfill.timeframes = ["1m"]
    src = FixtureCandleSource(fixture_csv)

    report = backfill(config, src, store, symbols=["MNQ"])
    # backfill asks for 500; the fixture holds 480 closed bars and returns them all.
    assert report.written["MNQ/1m"] == 480
    assert "MNQ/5m" in report.written  # derived timeframes materialized
    assert "MNQ/15m" in report.written
    assert not report.errors or all("MES" in e for e in report.errors)

    df5 = store.get_candles_df("MNQ", "5m")
    assert not df5.empty
    assert set(df5["source"]) == {"resampled"}


def test_backfill_reports_errors_but_continues(config, store, fixture_csv):
    config.data.backfill.timeframes = ["1m"]
    src = FixtureCandleSource(fixture_csv)
    report = backfill(config, src, store, symbols=["MNQ", "MES"])  # MES not in fixture
    assert report.written.get("MNQ/1m")
    assert any("MES/1m" in e and "SYMBOL_MISMATCH" in e for e in report.errors)


def test_collect_once_is_incremental(config, store, fixture_csv):
    src = FixtureCandleSource(fixture_csv)
    r1 = collect_once(config, src, store, symbols=["MNQ"])
    assert r1.written["MNQ/1m"] > 0
    r2 = collect_once(config, src, store, symbols=["MNQ"])  # same data again
    assert r2.written.get("MNQ/1m", 0) == 0  # nothing new


def test_status_reports_coverage(config, store, fixture_csv):
    src = FixtureCandleSource(fixture_csv)
    collect_once(config, src, store, symbols=["MNQ"])
    s = status(config, store)
    tfs = {row["timeframe"] for row in s["coverage"]}
    assert "1m" in tfs and "5m" in tfs

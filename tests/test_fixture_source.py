import pytest

from futures_copilot.data.fixtures import FixtureCandleSource, load_fixture_csv
from futures_copilot.errors import FixtureError, SymbolMismatch


def test_load_fixture_csv(fixture_csv):
    candles = load_fixture_csv(fixture_csv)
    assert len(candles) == 480
    assert all(c.source == "fixture" for c in candles)
    assert candles == sorted(candles, key=lambda c: c.ts)


def test_source_returns_most_recent_ascending(fixture_csv):
    src = FixtureCandleSource(fixture_csv)
    out = src.get_candles("MNQ", "1m", 50)
    assert len(out) == 50
    all_bars = load_fixture_csv(fixture_csv)
    assert out == all_bars[-50:]


def test_source_rejects_unknown_symbol(fixture_csv):
    src = FixtureCandleSource(fixture_csv)
    with pytest.raises(SymbolMismatch):
        src.get_candles("MES", "1m", 10)


def test_health_check_flags_fixture_mode(fixture_csv):
    src = FixtureCandleSource(fixture_csv)
    info = src.health_check()
    assert "FIXTURE" in info["warning"]


def test_missing_file_raises(tmp_path):
    with pytest.raises(FixtureError):
        FixtureCandleSource(tmp_path / "missing.csv")


def test_bad_columns_raise(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("a,b,c\n1,2,3\n")
    with pytest.raises(FixtureError):
        load_fixture_csv(p)

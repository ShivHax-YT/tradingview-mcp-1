import pytest

from futures_copilot.errors import UnsupportedTimeframe
from futures_copilot.models import Candle
from futures_copilot.data.resample import resample_1m, tf_seconds


def bar(ts: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> Candle:
    return Candle(symbol="MNQ", timeframe="1m", ts=ts, open=o, high=h, low=l, close=c, volume=v, source="fixture")


BASE = 1_750_000_200  # aligned to 5m boundary? 1_750_000_200 % 300 == 0 -> yes


def test_resample_5m_aggregation_correct():
    bars = [
        bar(BASE + 0 * 60, 100, 105, 99, 101, 10),
        bar(BASE + 1 * 60, 101, 108, 100, 107, 10),
        bar(BASE + 2 * 60, 107, 107, 95, 96, 10),
        bar(BASE + 3 * 60, 96, 99, 96, 98, 10),
        bar(BASE + 4 * 60, 98, 103, 97, 102, 10),
    ]
    out = resample_1m(bars, "5m")
    assert len(out) == 1
    b = out[0]
    assert b.ts == BASE
    assert b.open == 100 and b.close == 102
    assert b.high == 108 and b.low == 95
    assert b.volume == 50
    assert b.timeframe == "5m" and b.source == "resampled"


def test_incomplete_window_is_dropped():
    bars = [bar(BASE + i * 60, 100, 101, 99, 100) for i in range(7)]  # 5 complete + 2 extra
    out = resample_1m(bars, "5m")
    assert len(out) == 1  # second window has only 2/5 bars -> dropped
    assert out[0].ts == BASE


def test_gap_in_window_drops_that_window():
    bars = [bar(BASE + i * 60, 100, 101, 99, 100) for i in range(10) if i != 2]
    out = resample_1m(bars, "5m")
    # first window is missing minute 2 -> dropped; second window complete
    assert len(out) == 1
    assert out[0].ts == BASE + 300


def test_resample_15m_and_1h_counts():
    bars = [bar(BASE + i * 60, 100, 101, 99, 100) for i in range(120)]
    assert len(resample_1m(bars, "15m")) >= 7
    assert len(resample_1m(bars, "1h")) >= 1


def test_duplicate_input_bars_not_double_counted():
    bars = [bar(BASE + i * 60, 100, 101, 99, 100, v=10) for i in range(5)]
    out = resample_1m(bars + bars, "5m")
    assert len(out) == 1
    assert out[0].volume == 50  # not 100


def test_rejects_non_1m_input():
    b = Candle(symbol="MNQ", timeframe="5m", ts=BASE, open=1, high=2, low=0.5, close=1.5, volume=1, source="fixture")
    with pytest.raises(UnsupportedTimeframe):
        resample_1m([b], "15m")


def test_rejects_unknown_target():
    with pytest.raises(UnsupportedTimeframe):
        resample_1m([], "2m")


def test_tf_seconds():
    assert tf_seconds("1m") == 60
    assert tf_seconds("4h") == 14400
    with pytest.raises(UnsupportedTimeframe):
        tf_seconds("1w")

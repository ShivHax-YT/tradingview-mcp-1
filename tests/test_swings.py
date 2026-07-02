from futures_copilot.features.swings import find_swings, swings_confirmed_by

from .feature_helpers import minute_bars

T0 = 1_750_000_000


def _df():
    # (o, h, l, c) — swing high at i2 (106), swing low at i2's mirror scenario below
    return minute_bars(
        T0,
        [
            (100, 101, 99.0, 100),
            (100, 104, 99.5, 103),
            (103, 106, 101.0, 105),   # fractal high (k=2)
            (105, 104, 100.0, 103),
            (103, 101, 99.8, 100),
        ],
    )


def test_swing_high_detected_with_confirmation_lag():
    swings = find_swings(_df(), k=2, tf_seconds=60)
    highs = [s for s in swings if s.kind == "high"]
    assert len(highs) == 1
    s = highs[0]
    assert s.price == 106.0
    assert s.bar_ts == T0 + 120
    # Confirmed only when bar i+k (i4) CLOSES: ts4 + 60
    assert s.confirmed_close_ts == T0 + 240 + 60


def test_no_lookahead_filter():
    swings = find_swings(_df(), k=2, tf_seconds=60)
    assert swings_confirmed_by(swings, T0 + 240) == []          # i4 still open
    assert len(swings_confirmed_by(swings, T0 + 300)) >= 1      # i4 closed


def test_swing_low_detected():
    df = minute_bars(
        T0,
        [
            (100, 101, 99.0, 100),
            (100, 100.5, 98.0, 99),
            (99, 99.5, 96.0, 97),    # fractal low
            (97, 100.0, 98.0, 99),
            (99, 101.0, 99.0, 100),
        ],
    )
    lows = [s for s in find_swings(df, k=2, tf_seconds=60) if s.kind == "low"]
    assert len(lows) == 1
    assert lows[0].price == 96.0


def test_flat_series_has_no_swings():
    df = minute_bars(T0, [(100, 101, 99, 100)] * 7)
    assert find_swings(df, k=2, tf_seconds=60) == []

from futures_copilot.features.fvg import detect_fvgs

from .feature_helpers import minute_bars

T0 = 1_750_000_000


def _bullish_lifecycle_df():
    return minute_bars(
        T0,
        [
            (99.0, 100.0, 98.0, 99.5),      # i0: high 100
            (99.5, 105.0, 99.0, 104.0),     # i1: displacement
            (104.0, 106.0, 101.0, 105.0),   # i2: low 101 > 100 -> bullish FVG [100, 101]
            (105.0, 106.0, 100.5, 105.5),   # i3: dips into zone -> mitigated
            (105.5, 105.6, 99.0, 99.2),     # i4: traverses + closes below 100 -> filled + inverted
        ],
    )


def test_bullish_fvg_detected_with_zone():
    fvgs = detect_fvgs(_bullish_lifecycle_df(), tf_seconds=60)
    assert len(fvgs) == 1
    g = fvgs[0]
    assert g.kind == "bullish"
    assert g.bottom == 100.0 and g.top == 101.0
    assert g.created_bar_ts == T0 + 120
    assert g.created_close_ts == T0 + 180


def test_fvg_lifecycle_timestamps():
    # Lifecycle events stamp the CLOSE of the causing bar (closed-candle knowledge rule):
    # mitigating bar i3 opens at T0+180 and closes at T0+240; i4 closes at T0+300.
    g = detect_fvgs(_bullish_lifecycle_df(), tf_seconds=60)[0]
    assert g.mitigated_ts == T0 + 4 * 60
    assert g.filled_ts == T0 + 5 * 60
    assert g.inverted_ts == T0 + 5 * 60


def test_fvg_status_is_as_of_aware():
    g = detect_fvgs(_bullish_lifecycle_df(), tf_seconds=60)[0]
    assert g.status(T0 + 179) == "nonexistent"   # third bar not closed yet
    assert g.status(T0 + 180) == "open"
    assert g.status(T0 + 4 * 60) == "mitigated"  # i3 has closed
    assert g.status(T0 + 5 * 60) == "inverted"   # i4 closed; inversion outranks filled


def test_bearish_fvg_detected():
    df = minute_bars(
        T0,
        [
            (101.0, 102.0, 100.0, 100.5),   # i0: low 100
            (100.5, 100.8, 95.0, 95.5),     # i1: displacement down
            (95.5, 98.0, 94.0, 95.0),       # i2: high 98 < 100 -> bearish FVG [98, 100]
        ],
    )
    fvgs = detect_fvgs(df, tf_seconds=60)
    assert len(fvgs) == 1
    g = fvgs[0]
    assert g.kind == "bearish" and g.bottom == 98.0 and g.top == 100.0
    assert g.status(T0 + 180) == "open"


def test_min_size_filter():
    fvgs = detect_fvgs(_bullish_lifecycle_df(), tf_seconds=60, min_size=2.0)
    assert fvgs == []  # gap is exactly 1.0 point


def test_four_tick_strategy_threshold_filters_one_tick_but_keeps_six_ticks():
    tiny = minute_bars(T0, [
        (99, 100, 98, 99), (99, 104, 99, 103), (103, 105, 100.25, 104),
    ])
    large = minute_bars(T0, [
        (99, 100, 98, 99), (99, 104, 99, 103), (103, 105, 101.5, 104),
    ])
    min_size = 4 * 0.25
    assert detect_fvgs(tiny, tf_seconds=60, min_size=min_size) == []
    assert len(detect_fvgs(large, tf_seconds=60, min_size=min_size)) == 1


def test_no_fvg_in_overlapping_bars():
    df = minute_bars(T0, [(100, 102, 99, 101)] * 5)
    assert detect_fvgs(df, tf_seconds=60) == []

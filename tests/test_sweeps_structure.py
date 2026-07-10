import pytest

from futures_copilot.features.structure import detect_cisd, detect_mss
from futures_copilot.features.sweeps import detect_reclaim, detect_sweep
from futures_copilot.features.swings import find_swings

from .feature_helpers import minute_bars

T0 = 1_750_000_000
LEVEL = 100.0


def test_sell_side_sweep_and_reclaim():
    df = minute_bars(
        T0,
        [
            (101.0, 101.5, 100.5, 101.0),   # above the level
            (101.0, 101.2, 99.5, 99.8),     # sweeps below 100, closes below -> no same-bar reclaim
            (99.8, 100.9, 99.6, 100.6),     # closes back above -> reclaim (2 candles)
        ],
    )
    sweep = detect_sweep(df, LEVEL, side="sell", level_name="asia_low")[0]
    assert sweep is not None and sweep.idx == 1 and sweep.extreme == 99.5

    reclaim = detect_reclaim(df, sweep, max_candles=3)
    assert reclaim is not None
    assert reclaim.candles_after_sweep == 2
    assert reclaim.close == 100.6


def test_reclaim_window_too_short_returns_none():
    df = minute_bars(
        T0,
        [
            (101.0, 101.5, 100.5, 101.0),
            (101.0, 101.2, 99.5, 99.8),
            (99.8, 100.9, 99.6, 100.6),
        ],
    )
    sweep = detect_sweep(df, LEVEL, side="sell")[0]
    assert detect_reclaim(df, sweep, max_candles=1) is None


def test_wick_through_reclaims_same_bar():
    df = minute_bars(T0, [(101.0, 101.5, 99.5, 100.4)])  # stop-run candle
    sweep = detect_sweep(df, LEVEL, side="sell")[0]
    reclaim = detect_reclaim(df, sweep, max_candles=3)
    assert reclaim is not None and reclaim.candles_after_sweep == 1


def test_buy_side_sweep_and_bearish_reclaim():
    level = 110.0
    df = minute_bars(
        T0,
        [
            (109.0, 109.5, 108.5, 109.2),
            (109.2, 111.0, 109.0, 109.4),   # wick above 110, close back below -> sweep+reclaim
        ],
    )
    sweep = detect_sweep(df, level, side="buy", level_name="pdh")[0]
    assert sweep is not None and sweep.extreme == 111.0
    reclaim = detect_reclaim(df, sweep, max_candles=3)
    assert reclaim is not None and reclaim.candles_after_sweep == 1


def test_no_sweep_when_level_holds():
    df = minute_bars(T0, [(101, 102, 100.5, 101.5)] * 5)
    assert detect_sweep(df, LEVEL, side="sell") == []


def test_invalid_side_raises():
    df = minute_bars(T0, [(101, 102, 100.5, 101.5)])
    with pytest.raises(ValueError):
        detect_sweep(df, LEVEL, side="up")


def test_detect_sweep_returns_distinct_rearmed_pierces():
    df = minute_bars(T0, [
        (101, 102, 99, 99.5),
        (99.5, 100, 98.5, 99.8),
        (99.8, 101, 99.7, 100.5),  # closes back above and rearms
        (100.5, 101, 99.2, 100.4),
    ])
    sweeps = detect_sweep(df, LEVEL, side="sell")
    assert [s.idx for s in sweeps] == [0, 3]


def test_swing_k_three_ignores_two_bar_noise_pivot():
    df = minute_bars(T0, [
        (100, 100.0, 99, 100), (100, 100.25, 99, 100),
        (100, 100.5, 99, 100), (100, 100.25, 99, 100),
        (100, 100.0, 99, 100), (100, 101.0, 99, 100),
        (100, 100.5, 99, 100),
    ])
    assert not [s for s in find_swings(df, k=3, tf_seconds=60) if s.kind == "high" and s.price == 100.5]


# ── MSS ─────────────────────────────────────────────────────────────────────

def test_bullish_mss_breaks_confirmed_swing_high():
    df = minute_bars(
        T0,
        [
            (100, 101, 98.0, 100),
            (100, 104, 99.0, 103),
            (103, 106, 101.0, 105),   # swing high 106 (confirmed at close of i4)
            (105, 104, 100.0, 103),
            (103, 101, 99.5, 100),
            (100, 103, 99.8, 102),    # below 106 -> no break
            (102, 108, 101.0, 107),   # closes above 106 -> MSS
        ],
    )
    swings = find_swings(df, k=2, tf_seconds=60)
    mss = detect_mss(df, swings, "bullish", tf_seconds=60)
    assert mss is not None
    assert mss.ts == T0 + 6 * 60
    assert mss.level == 106.0


def test_no_mss_when_no_break():
    df = minute_bars(
        T0,
        [
            (100, 101, 98, 100),
            (100, 104, 99, 103),
            (103, 106, 101, 105),
            (105, 104, 100, 103),
            (103, 101, 99, 100),
            (100, 102, 99, 101),
        ],
    )
    swings = find_swings(df, k=2, tf_seconds=60)
    assert detect_mss(df, swings, "bullish", tf_seconds=60) is None


def test_bearish_mss():
    df = minute_bars(
        T0,
        [
            (100, 102.0, 99.0, 100),
            (100, 101.0, 96.0, 97),
            (97, 98.0, 94.0, 95),     # swing low 94
            (95, 99.0, 95.0, 98),
            (98, 101.0, 97.0, 100),
            (100, 100.5, 95.0, 96),   # above 94 -> no break
            (96, 97.0, 92.0, 93),     # closes below 94 -> MSS
        ],
    )
    swings = find_swings(df, k=2, tf_seconds=60)
    mss = detect_mss(df, swings, "bearish", tf_seconds=60)
    assert mss is not None and mss.level == 94.0 and mss.ts == T0 + 6 * 60


# ── CISD ────────────────────────────────────────────────────────────────────

def _cisd_df():
    return minute_bars(
        T0,
        [
            (100, 102, 99, 101),    # up
            (110, 111, 107, 108),   # down series starts: open 110
            (108, 109, 104, 105),   # down
            (105, 106, 100, 101),   # down — anchor (sweep bar)
            (101, 105, 100, 104),   # up but 104 < 110 -> not CISD
            (104, 112, 103, 111),   # closes above 110 -> CISD
        ],
    )


def test_bullish_cisd_threshold_is_series_open():
    ev = detect_cisd(_cisd_df(), "bullish", anchor_idx=3)
    assert ev is not None
    assert ev.level == 110.0
    assert ev.ts == T0 + 5 * 60


def test_cisd_anchor_on_reversal_bar_walks_back():
    ev = detect_cisd(_cisd_df(), "bullish", anchor_idx=4)  # anchor is an up-close bar
    assert ev is not None and ev.level == 110.0


def test_bearish_cisd():
    df = minute_bars(
        T0,
        [
            (100, 101, 98, 99),     # down
            (90, 93, 89, 92),       # up series starts: open 90
            (92, 96, 91, 95),       # up
            (95, 99, 94, 98),       # up — anchor
            (98, 99, 93, 94),       # 94 > 90 -> not yet
            (94, 95, 88, 89),       # closes below 90 -> CISD
        ],
    )
    ev = detect_cisd(df, "bearish", anchor_idx=3)
    assert ev is not None and ev.level == 90.0 and ev.ts == T0 + 5 * 60


def test_cisd_none_when_no_series():
    df = minute_bars(T0, [(100, 102, 99, 101)] * 4)  # all up-close
    assert detect_cisd(df, "bullish", anchor_idx=3) is None

import math

import pytest

from futures_copilot.features.pricing import (
    points_to_dollars,
    points_to_ticks,
    position_in_range,
    premium_discount,
    rr,
)
from futures_copilot.features.volatility import atr
from futures_copilot.features.vwap import anchored_vwap

from .feature_helpers import minute_bars

T0 = 1_750_000_000


# ── VWAP ────────────────────────────────────────────────────────────────────

def test_vwap_hand_calculation():
    df = minute_bars(T0, [(10, 10, 8, 9, 1), (11, 12, 10, 11, 3)])
    v = anchored_vwap(df)
    # tp1=9 (v=1); tp2=11 (v=3) -> vwap2 = (9*1 + 11*3)/4 = 10.5
    assert v.iloc[0] == pytest.approx(9.0)
    assert v.iloc[1] == pytest.approx(10.5)


def test_vwap_zero_volume_falls_back_to_mean_tp():
    df = minute_bars(T0, [(10, 10, 8, 9, 0), (11, 12, 10, 11, 0)])
    v = anchored_vwap(df)
    assert v.iloc[0] == pytest.approx(9.0)
    assert v.iloc[1] == pytest.approx(10.0)  # mean of tp 9 and 11
    assert not math.isnan(v.iloc[1])


# ── ATR ─────────────────────────────────────────────────────────────────────

def test_atr_constant_range():
    df = minute_bars(T0, [(100, 102, 100, 101)] * 20)
    a = atr(df, period=14)
    assert a.iloc[-1] == pytest.approx(2.0)


def test_atr_wilder_recursion():
    # TRs: bar0 h-l = 2; bar1 max(4, |h-pc|=3, |l-pc|=1) = 4
    df = minute_bars(T0, [(100, 102, 100, 101), (102, 104, 100, 103)])
    a = atr(df, period=2)
    # RMA(alpha=.5): a0=2; a1 = .5*4 + .5*2 = 3
    assert a.iloc[0] == pytest.approx(2.0)
    assert a.iloc[1] == pytest.approx(3.0)


def test_atr_uses_gap_true_range():
    # Second bar gaps far above the first; TR must use |high - prev_close|.
    df = minute_bars(T0, [(100, 101, 99, 100), (110, 111, 110, 110.5)])
    a = atr(df, period=1)  # period 1 -> ATR == TR
    assert a.iloc[1] == pytest.approx(11.0)  # 111 - 100


def test_atr_invalid_period():
    df = minute_bars(T0, [(100, 102, 100, 101)])
    with pytest.raises(ValueError):
        atr(df, period=0)


# ── R:R and range position ──────────────────────────────────────────────────

def test_rr_long_and_short():
    assert rr(entry=100, stop=98, target=103) == pytest.approx(1.5)
    assert rr(entry=100, stop=102, target=97) == pytest.approx(1.5)


def test_rr_rejects_nonsense_geometry():
    with pytest.raises(ValueError):
        rr(entry=100, stop=98, target=99.0 - 1.0)  # target below entry with stop below
    with pytest.raises(ValueError):
        rr(entry=100, stop=100, target=105)        # zero risk
    with pytest.raises(ValueError):
        rr(entry=100, stop=98, target=100)         # zero reward


def test_position_and_premium_discount():
    assert position_in_range(105, 90, 110) == pytest.approx(0.75)
    assert premium_discount(105, 90, 110) == "premium"
    assert premium_discount(92, 90, 110) == "discount"
    assert premium_discount(100, 90, 110) == "equilibrium"
    with pytest.raises(ValueError):
        position_in_range(100, 110, 90)


def test_tick_and_dollar_conversion(config):
    mnq = config.symbols["MNQ"]
    mes = config.symbols["MES"]
    assert points_to_ticks(10, mnq) == pytest.approx(40)
    assert points_to_dollars(10, mnq) == pytest.approx(20.0)        # 40 ticks * $0.50
    assert points_to_dollars(10, mes, contracts=2) == pytest.approx(100.0)  # 40 * $1.25 * 2

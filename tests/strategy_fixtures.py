"""Shared seeding for strategy/gate/packet tests.

Backdrop (15m, like a real backfill) for trading day 2026-06-24:
  prior day 06-23: H=23100, L=22900
  asia    (18:00-03:00): H=23080, L=22960
  london  (03:00-09:30): H=22995, L=22970
Morning stories are seeded as explicit 5m bars (the detection timeframe),
with aligned 15m aggregates and flat 1m fills so market state has a horizon.
"""

from __future__ import annotations

from futures_copilot.models import Candle

from .feature_helpers import et_ts

SYM = "MNQ"


def c(tf: str, ts: int, o: float, h: float, l: float, cl: float, v: float = 500.0) -> Candle:
    return Candle(symbol=SYM, timeframe=tf, ts=ts, open=o, high=h, low=l,
                  close=cl, volume=v, source="fixture")


def seed_backdrop(store) -> None:
    candles = []
    # prior trading day on 15m: 06-22 18:00 -> 06-23 17:00 (92 bars)
    start = et_ts(2026, 6, 22, 18, 0)
    for i in range(92):
        hi = 23100.0 if i == 40 else 23020.0
        lo = 22900.0 if i == 60 else 22980.0
        candles.append(c("15m", start + i * 900, 23000, hi, lo, 23000, v=800))
    # current day overnight on 15m: 18:00 -> 09:15 open (62 bars)
    start = et_ts(2026, 6, 23, 18, 0)
    for i in range(62):
        if i < 36:                                # asia 18:00-03:00
            hi, lo = (23080.0, 22975.0) if i == 30 else (23040.0, 22960.0)
        else:                                     # london 03:00-09:30
            hi, lo = 22995.0, 22970.0
        o = cl = min(max(23000.0, lo + 5), hi - 5)
        candles.append(c("15m", start + i * 900, o, hi, lo, o, v=800))
    store.upsert_candles(candles)


def _seed_morning(store, bars5: list[tuple], bars15: list[tuple]) -> None:
    """bars are (et_hour, et_min, o, h, l, c)."""
    candles = []
    for h, m, o, hi, lo, cl in bars5:
        ts = et_ts(2026, 6, 24, h, m)
        candles.append(c("5m", ts, o, hi, lo, cl))
        for k in range(5):                        # flat 1m fills for horizon/vwap
            candles.append(c("1m", ts + k * 60, o, hi, lo, cl, v=100))
    for h, m, o, hi, lo, cl in bars15:
        candles.append(c("15m", et_ts(2026, 6, 24, h, m), o, hi, lo, cl, v=1500))
    store.upsert_candles(candles)


LONG_TRAP_5M = [
    # drift down into the asia low (22960): consecutive down-closes = delivery
    (9, 30, 23000, 23002, 22988, 22990),
    (9, 35, 22990, 22992, 22978, 22980),
    (9, 40, 22980, 22995, 22974, 22976),
    (9, 45, 22976, 22999, 22970, 22974),   # local peak 22999 -> confirmed 5m swing high
    (9, 50, 22974, 22980, 22966, 22968),   # sweeps london low 22970 en route
    (9, 55, 22968, 22975, 22965, 22966),
    (10, 0, 22966, 22975, 22945, 22972),   # SWEEP asia low (wick 22945) + reclaim close 22972
    (10, 5, 22972, 23005, 22970, 23002),   # MSS (>22999) + CISD (>23000) on one close
    (10, 10, 23002, 23020, 23000, 23018),  # impulse leaves bullish FVG [22975, 23000]
    (10, 15, 23018, 23025, 23005, 23010),  # gap untouched
    (10, 20, 23010, 23015, 22998, 23004),  # retest dips into the gap -> mitigated
]
LONG_TRAP_15M = [
    (9, 30, 23000, 23002, 22974, 22976),
    (9, 45, 22976, 22999, 22965, 22966),
    (10, 0, 22966, 23020, 22945, 23018),
    (10, 15, 23018, 23025, 22998, 23004),
]


def seed_long_trap(store) -> None:
    seed_backdrop(store)
    _seed_morning(store, LONG_TRAP_5M, LONG_TRAP_15M)


SHORT_TRAP_5M = [
    # rally into the asia high (23080): up-close delivery
    (9, 30, 23000, 23015, 22998, 23012),
    (9, 35, 23012, 23030, 23008, 23026),
    (9, 40, 23026, 23044, 23022, 23040),
    (9, 45, 23040, 23052, 23036, 23038),
    (9, 50, 23038, 23048, 23020, 23032),   # local dip 23020 -> confirmed 5m swing low
    (9, 55, 23032, 23062, 23028, 23058),
    (10, 0, 23058, 23095, 23054, 23066),   # SWEEP asia high (wick 23095) + reclaim close 23066
    (10, 5, 23066, 23068, 23008, 23012),   # MSS (<23020) + CISD (<23032) on one close
    (10, 10, 23012, 23016, 22996, 23004),  # impulse leaves bearish FVG [23016, 23054]
    (10, 15, 23004, 23020, 22998, 23008),  # wick back up into the gap -> mitigated
]
SHORT_TRAP_15M = [
    (9, 30, 23000, 23044, 22998, 23040),
    (9, 45, 23040, 23062, 23020, 23058),
    (10, 0, 23058, 23095, 22996, 23004),
    (10, 15, 23004, 23020, 22998, 23008),
]


def seed_short_trap(store) -> None:
    seed_backdrop(store)
    _seed_morning(store, SHORT_TRAP_5M, SHORT_TRAP_15M)


ORB_5M = [
    (9, 30, 23000, 23010, 22990, 23005),
    (9, 35, 23005, 23009, 22995, 23002),
    (9, 40, 23002, 23008, 22994, 23004),
    (9, 45, 23004, 23008, 22996, 23004),
    (9, 50, 23004, 23009, 22997, 23006),
    (9, 55, 23006, 23009, 22999, 23007),
    (10, 0, 23007, 23020, 23005, 23018),   # confirmed close above OR high 23010
    (10, 5, 23016, 23019, 23008, 23016),   # retest of the edge, holds
    (10, 10, 23016, 23024, 23012, 23022),
]
ORB_15M = [
    (9, 30, 23000, 23010, 22990, 23004),
    (9, 45, 23004, 23009, 22996, 23007),
    (10, 0, 23007, 23024, 23005, 23022),
]


def seed_orb_breakout(store, fakeout: bool = False) -> None:
    seed_backdrop(store)
    bars5 = list(ORB_5M)
    if fakeout:
        bars5[7] = (10, 5, 23016, 23019, 23000, 23002)   # close back inside the range
    _seed_morning(store, bars5, ORB_15M)

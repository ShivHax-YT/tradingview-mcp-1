from futures_copilot.models import Candle


def make_candle(ts: int, tf: str = "1m", symbol: str = "MNQ", price: float = 23000.0) -> Candle:
    return Candle(
        symbol=symbol, timeframe=tf, ts=ts,
        open=price, high=price + 5, low=price - 5, close=price + 1,
        volume=100, source="fixture",
    )


def test_upsert_and_read_roundtrip(store):
    candles = [make_candle(1_750_000_000 + i * 60) for i in range(10)]
    assert store.upsert_candles(candles) == 10
    df = store.get_candles_df("MNQ", "1m")
    assert len(df) == 10
    assert list(df["ts"]) == sorted(df["ts"])
    assert df.index.tz is not None  # UTC-aware index


def test_upsert_is_idempotent(store):
    candles = [make_candle(1_750_000_000 + i * 60) for i in range(5)]
    store.upsert_candles(candles)
    store.upsert_candles(candles)  # same bars again
    df = store.get_candles_df("MNQ", "1m")
    assert len(df) == 5  # no duplicates


def test_upsert_updates_changed_bar(store):
    c = make_candle(1_750_000_000)
    store.upsert_candles([c])
    revised = c.model_copy(update={"close": 23099.0, "high": 23100.0})
    store.upsert_candles([revised])
    df = store.get_candles_df("MNQ", "1m")
    assert len(df) == 1
    assert df.iloc[0]["close"] == 23099.0


def test_last_ts_and_coverage(store):
    store.upsert_candles([make_candle(1_750_000_000 + i * 60) for i in range(3)])
    assert store.last_ts("MNQ", "1m") == 1_750_000_000 + 120
    assert store.last_ts("MES", "1m") is None
    cov = store.coverage()
    assert cov[0]["symbol"] == "MNQ" and cov[0]["bars"] == 3


def test_gap_detection(store):
    ts0 = 1_750_000_000
    bars = [make_candle(ts0), make_candle(ts0 + 60), make_candle(ts0 + 5 * 60)]  # 3 missing
    store.upsert_candles(bars)
    gaps = store.gaps("MNQ", "1m", 60)
    assert len(gaps) == 1
    assert gaps[0]["missing_bars"] == 3

"""Collection orchestration: backfill + incremental collect into SQLite.

Backfill strategy (constrained by the bridge: data_get_ohlcv returns the
NEWEST <=500 bars of the chart's active timeframe):
  - pull each configured timeframe natively (1m ≈ 8h, 5m ≈ 1.7d, 15m ≈ 5d, 1h ≈ 3wk)
  - store all natively; deep history exists at coarser resolution, recent
    history at full 1m resolution. Feature code (Phase 2) uses the finest
    timeframe available for each lookback window.

Incremental collect:
  - poll 1m bars, upsert new closed bars, then refresh derived 3m/5m/15m/1h
    bars via the resampler (source='resampled'; native tvmcp bars are never
    overwritten by resampled ones for the same key thanks to upsert order:
    resample writes happen only for windows newer than the native backfill).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config import Config
from ..db.store import Store
from ..errors import DataSourceError
from .base import CandleSource
from .resample import TF_SECONDS, resample_1m

DERIVED_TFS = ["3m", "5m", "15m", "1h"]


@dataclass
class CollectReport:
    written: dict[str, int] = field(default_factory=dict)  # "MNQ/1m" -> bars written
    errors: list[str] = field(default_factory=list)

    def add(self, symbol: str, timeframe: str, n: int) -> None:
        key = f"{symbol}/{timeframe}"
        self.written[key] = self.written.get(key, 0) + n

    def summary(self) -> str:
        parts = [f"{k}: +{v}" for k, v in sorted(self.written.items())]
        out = "; ".join(parts) if parts else "no new bars"
        if self.errors:
            out += f" | ERRORS: {' || '.join(self.errors)}"
        return out


def backfill(config: Config, source: CandleSource, store: Store, symbols: list[str] | None = None) -> CollectReport:
    """One-time/occasional deep pull of every configured timeframe, natively."""
    report = CollectReport()
    symbols = symbols or list(config.symbols.keys())
    n_bars = config.data.backfill.bars_per_timeframe
    for symbol in symbols:
        for tf in config.data.backfill.timeframes:
            try:
                candles = source.get_candles(symbol, tf, n_bars)
                report.add(symbol, tf, store.upsert_candles(candles))
            except DataSourceError as e:
                # Loud and precise, but keep going: a failure on MES/1h must not
                # silently discard successful MNQ/1m data.
                report.errors.append(f"{symbol}/{tf}: {e}")
    _refresh_derived(config, store, report, symbols)
    return report


def collect_once(config: Config, source: CandleSource, store: Store, symbols: list[str] | None = None) -> CollectReport:
    """One incremental pass: newest 1m bars for each symbol, then derived TFs."""
    report = CollectReport()
    symbols = symbols or list(config.symbols.keys())
    tf = config.data.collect.timeframe
    for symbol in symbols:
        try:
            last = store.last_ts(symbol, tf)
            candles = source.get_candles(symbol, tf, config.data.tvmcp.max_bars_per_call - 1)
            source.assert_live_freshness(symbol, tf)
            if last is not None:
                candles = [c for c in candles if c.ts > last]
            report.add(symbol, tf, store.upsert_candles(candles))
        except DataSourceError as e:
            report.errors.append(f"{symbol}/{tf}: {e}")
    _refresh_derived(config, store, report, symbols)
    return report


def collect_loop(config: Config, source: CandleSource, store: Store, symbols: list[str] | None = None) -> None:
    """Run collect_once forever at the configured interval. Ctrl+C to stop."""
    interval = config.data.collect.interval_s
    print(f"collecting every {interval}s — Ctrl+C to stop")
    while True:
        report = collect_once(config, source, store, symbols)
        print(f"[{time.strftime('%H:%M:%S')}] {report.summary()}")
        time.sleep(interval)


def _refresh_derived(config: Config, store: Store, report: CollectReport, symbols: list[str]) -> None:
    """Rebuild derived bars from recent 1m data.

    Only windows strictly NEWER than the last native (tvmcp-backfilled) bar of the
    target timeframe are written, so resampled bars extend native history instead
    of overwriting it.
    """
    for symbol in symbols:
        one_m = store.get_candles_df(symbol, "1m")
        if one_m.empty:
            continue
        from ..models import Candle

        candles_1m = [
            Candle(
                symbol=symbol, timeframe="1m", ts=int(r.ts),
                open=r.open, high=r.high, low=r.low, close=r.close,
                volume=r.volume, source=r.source,
            )
            for r in one_m.itertuples()
        ]
        for tf in DERIVED_TFS:
            derived = resample_1m(candles_1m, tf)
            last_native = _last_native_ts(store, symbol, tf)
            fresh = [c for c in derived if last_native is None or c.ts > last_native]
            if fresh:
                report.add(symbol, tf, store.upsert_candles(fresh))


def _last_native_ts(store: Store, symbol: str, timeframe: str) -> int | None:
    row = store.conn.execute(
        "SELECT MAX(ts) FROM candles WHERE symbol=? AND timeframe=? AND source='tvmcp'",
        (symbol, timeframe),
    ).fetchone()
    return row[0]


def status(config: Config, store: Store) -> dict:
    cov = store.coverage()
    gap_info = {}
    for row in cov:
        tf = row["timeframe"]
        if tf in TF_SECONDS:
            g = store.gaps(row["symbol"], tf, TF_SECONDS[tf], max_report=3)
            if g:
                gap_info[f"{row['symbol']}/{tf}"] = g
    return {"coverage": cov, "largest_gaps": gap_info, "db": str(config.db_file)}

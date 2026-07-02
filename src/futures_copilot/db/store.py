"""SQLite store. Boring, local, inspectable — by design."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..models import Candle

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
SCHEMA_VERSION = "1"


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA foreign_keys = ON")
        try:
            # WAL = better concurrency (collector writing while dashboard reads).
            # Some filesystems (network mounts) can't do WAL; DELETE mode is fine there.
            self.conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            self.conn.execute("PRAGMA journal_mode = DELETE")

    # -- lifecycle -------------------------------------------------------------
    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- candles ---------------------------------------------------------------
    def upsert_candles(self, candles: list[Candle]) -> int:
        """Insert or replace bars. Returns number of bars written."""
        if not candles:
            return 0
        rows = [c.as_row() for c in candles]
        self.conn.executemany(
            """INSERT INTO candles (symbol, timeframe, ts, open, high, low, close, volume, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, timeframe, ts) DO UPDATE SET
                 open=excluded.open, high=excluded.high, low=excluded.low,
                 close=excluded.close, volume=excluded.volume, source=excluded.source""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_candles_df(
        self,
        symbol: str,
        timeframe: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Bars as a DataFrame indexed by UTC DatetimeIndex, ascending."""
        q = "SELECT ts, open, high, low, close, volume, source FROM candles WHERE symbol=? AND timeframe=?"
        params: list = [symbol, timeframe]
        if start_ts is not None:
            q += " AND ts >= ?"
            params.append(start_ts)
        if end_ts is not None:
            q += " AND ts <= ?"
            params.append(end_ts)
        q += " ORDER BY ts DESC"
        if limit is not None:
            q += " LIMIT ?"
            params.append(limit)
        df = pd.read_sql_query(q, self.conn, params=params)
        df = df.sort_values("ts").reset_index(drop=True)
        if not df.empty:
            df.index = pd.to_datetime(df["ts"], unit="s", utc=True)
            df.index.name = "time"
        return df

    def last_ts(self, symbol: str, timeframe: str) -> int | None:
        row = self.conn.execute(
            "SELECT MAX(ts) FROM candles WHERE symbol=? AND timeframe=?", (symbol, timeframe)
        ).fetchone()
        return row[0]

    def coverage(self) -> list[dict]:
        """Per (symbol, timeframe): bar count, first/last bar time (ISO UTC)."""
        rows = self.conn.execute(
            """SELECT symbol, timeframe, COUNT(*), MIN(ts), MAX(ts)
               FROM candles GROUP BY symbol, timeframe ORDER BY symbol, timeframe"""
        ).fetchall()

        def iso(ts: int | None) -> str | None:
            if ts is None:
                return None
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        return [
            {"symbol": s, "timeframe": tf, "bars": n, "first": iso(a), "last": iso(b)}
            for s, tf, n, a, b in rows
        ]

    def gaps(self, symbol: str, timeframe: str, tf_seconds: int, max_report: int = 20) -> list[dict]:
        """Detect missing-bar gaps (ignoring gaps <= 1 bar). Market closures show up too;
        callers should interpret with session context. Honest data > pretty data."""
        df = self.get_candles_df(symbol, timeframe)
        if len(df) < 2:
            return []
        ts = df["ts"].to_numpy()
        out = []
        for prev, nxt in zip(ts[:-1], ts[1:]):
            missing = int((nxt - prev) // tf_seconds) - 1
            if missing > 0:
                out.append(
                    {
                        "after": datetime.fromtimestamp(int(prev), tz=timezone.utc).isoformat(),
                        "missing_bars": missing,
                    }
                )
        out.sort(key=lambda g: g["missing_bars"], reverse=True)
        return out[:max_report]

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


    # -- market states / strategy outputs (Phase 3+) ----------------------------
    def save_market_state(self, symbol: str, ts: int, session: str | None,
                          current_price: float, json_state: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO market_states (symbol, ts, session, current_price, json_state) VALUES (?,?,?,?,?)",
            (symbol, ts, session, current_price, json_state),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def latest_market_state(self, symbol: str) -> dict | None:
        row = self.conn.execute(
            "SELECT id, symbol, ts, session, current_price, json_state FROM market_states "
            "WHERE symbol=? ORDER BY ts DESC, id DESC LIMIT 1", (symbol,),
        ).fetchone()
        if row is None:
            return None
        return {"id": row[0], "symbol": row[1], "ts": row[2], "session": row[3],
                "current_price": row[4], "json_state": row[5]}

    def save_strategy_output(self, symbol: str, ts: int, strategy_name: str,
                             direction: str | None, grade: str | None, json_output: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO strategy_outputs (symbol, ts, strategy_name, direction, grade, json_output) "
            "VALUES (?,?,?,?,?,?)",
            (symbol, ts, strategy_name, direction, grade, json_output),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def latest_strategy_outputs(self, symbol: str, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, symbol, ts, strategy_name, direction, grade, json_output "
            "FROM strategy_outputs WHERE symbol=? ORDER BY id DESC LIMIT ?", (symbol, limit),
        ).fetchall()
        return [
            {"id": r[0], "symbol": r[1], "ts": r[2], "strategy_name": r[3],
             "direction": r[4], "grade": r[5], "json_output": r[6]}
            for r in rows
        ]


    # -- signals (Phase 4+; decision written by the risk gate ONLY) -------------
    def save_signal(self, *, symbol: str, ts: int, session: str | None, trading_day: str,
                    decision: str, setup: str | None, grade: str | None,
                    entry_lo: float | None, entry_hi: float | None, stop: float | None,
                    tp1: float | None, tp2: float | None, rr: float | None,
                    reasons: str, warnings: str, invalidation: str, json_signal: str) -> int:
        cur = self.conn.execute(
            """INSERT INTO signals (symbol, ts, session, trading_day, decision, setup, grade,
                                    entry_lo, entry_hi, stop, tp1, tp2, rr,
                                    reasons, warnings, invalidation, json_signal)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (symbol, ts, session, trading_day, decision, setup, grade,
             entry_lo, entry_hi, stop, tp1, tp2, rr, reasons, warnings, invalidation, json_signal),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def signal_exists(self, symbol: str, ts: int, setup: str, direction: str,
                      swept_level: str | None = None) -> int | None:
        """Duplicate guard: same bar + setup + direction + swept level already
        gated. Two different levels trapped on the same confirmation bar are
        DIFFERENT signals and must both persist."""
        import json as _json

        rows = self.conn.execute(
            "SELECT id, json_signal FROM signals WHERE symbol=? AND ts=? AND setup=?",
            (symbol, ts, setup),
        ).fetchall()
        for rid, js in rows:
            try:
                cand = _json.loads(js).get("candidate") or {}
            except (TypeError, ValueError):
                cand = {}
            if cand.get("direction") != direction:
                continue
            if swept_level is not None and (cand.get("context") or {}).get("swept_level") != swept_level:
                continue
            return int(rid)
        return None

    def count_passing_signals(self, symbol: str, trading_day: str, session: str | None) -> int:
        q = ("SELECT COUNT(*) FROM signals WHERE symbol=? AND trading_day=? "
             "AND decision IN ('LONG','SHORT')")
        params: list = [symbol, trading_day]
        if session is not None:
            q += " AND session=?"
            params.append(session)
        return int(self.conn.execute(q, params).fetchone()[0])

    def latest_signals(self, symbol: str | None = None, limit: int = 20) -> list[dict]:
        q = ("SELECT id, symbol, ts, session, trading_day, decision, setup, grade, "
             "entry_lo, entry_hi, stop, tp1, tp2, rr, reasons, warnings, invalidation, json_signal "
             "FROM signals")
        params: list = []
        if symbol is not None:
            q += " WHERE symbol=?"
            params.append(symbol)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cols = ["id", "symbol", "ts", "session", "trading_day", "decision", "setup", "grade",
                "entry_lo", "entry_hi", "stop", "tp1", "tp2", "rr", "reasons", "warnings",
                "invalidation", "json_signal"]
        return [dict(zip(cols, r)) for r in self.conn.execute(q, params).fetchall()]

    def get_signal(self, signal_id: int) -> dict | None:
        rows = [r for r in self.latest_signals(limit=1_000_000) if r["id"] == signal_id]
        return rows[0] if rows else None


    # -- journal (Phase 5/6): bias, mistakes, reviews ---------------------------
    def set_daily_bias(self, trading_day: str, symbol: str, bias: str, notes: str = "") -> None:
        self.conn.execute(
            "INSERT INTO daily_bias (trading_day, symbol, bias, notes) VALUES (?,?,?,?) "
            "ON CONFLICT(trading_day, symbol) DO UPDATE SET bias=excluded.bias, notes=excluded.notes",
            (trading_day, symbol, bias, notes),
        )
        self.conn.commit()

    def get_daily_bias(self, trading_day: str, symbol: str) -> dict | None:
        row = self.conn.execute(
            "SELECT trading_day, symbol, bias, notes FROM daily_bias WHERE trading_day=? AND symbol=?",
            (trading_day, symbol),
        ).fetchone()
        if row is None:
            return None
        return {"trading_day": row[0], "symbol": row[1], "bias": row[2], "notes": row[3]}

    def add_mistake(self, tag: str, description: str = "", rule_update: str = "",
                    applies_to: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO mistakes (tag, description, rule_update, applies_to) VALUES (?,?,?,?)",
            (tag, description, rule_update, applies_to),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_mistakes(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, tag, description, rule_update, applies_to FROM mistakes "
            "ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
        cols = ["id", "tag", "description", "rule_update", "applies_to"]
        return [dict(zip(cols, r)) for r in rows]

    def add_trade_review(self, *, signal_id: int | None, symbol: str,
                         taken: bool, result_r: float | None = None,
                         max_favorable_r: float | None = None, max_adverse_r: float | None = None,
                         mistake_tags: str = "[]", notes: str = "",
                         ts_open: int | None = None, ts_close: int | None = None,
                         json_review: str = "{}") -> int:
        cur = self.conn.execute(
            """INSERT INTO trade_reviews (signal_id, symbol, ts_open, ts_close, taken,
                                          result_r, max_favorable_r, max_adverse_r,
                                          mistake_tags, notes, json_review)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (signal_id, symbol, ts_open, ts_close, int(taken), result_r,
             max_favorable_r, max_adverse_r, mistake_tags, notes, json_review),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def review_for_signal(self, signal_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT id, signal_id, taken, result_r, max_favorable_r, max_adverse_r, "
            "mistake_tags, notes FROM trade_reviews WHERE signal_id=? ORDER BY id DESC LIMIT 1",
            (signal_id,),
        ).fetchone()
        if row is None:
            return None
        cols = ["id", "signal_id", "taken", "result_r", "max_favorable_r", "max_adverse_r",
                "mistake_tags", "notes"]
        return dict(zip(cols, row))

    def list_trade_reviews(self, symbol: str | None = None, limit: int = 50) -> list[dict]:
        q = ("SELECT id, signal_id, symbol, taken, result_r, mistake_tags, notes "
             "FROM trade_reviews")
        params: list = []
        if symbol is not None:
            q += " WHERE symbol=?"
            params.append(symbol)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cols = ["id", "signal_id", "symbol", "taken", "result_r", "mistake_tags", "notes"]
        return [dict(zip(cols, r)) for r in self.conn.execute(q, params).fetchall()]

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

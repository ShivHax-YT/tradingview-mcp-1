-- Futures AI Chart Copilot — SQLite schema v1
-- All timestamps are UTC epoch seconds. Session logic converts via America/New_York.
-- (journal_mode is set by Store at connect time: WAL where supported, DELETE otherwise.)

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Canonical bar store. Holds natively-pulled bars (source='tvmcp'),
-- resampled bars (source='resampled'), and test bars (source='fixture').
-- ts = bar OPEN time, UTC epoch seconds.
CREATE TABLE IF NOT EXISTS candles (
  symbol    TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  ts        INTEGER NOT NULL,
  open      REAL NOT NULL,
  high      REAL NOT NULL,
  low       REAL NOT NULL,
  close     REAL NOT NULL,
  volume    REAL NOT NULL DEFAULT 0,
  source    TEXT NOT NULL DEFAULT 'unknown',
  PRIMARY KEY (symbol, timeframe, ts)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_candles_lookup
  ON candles (symbol, timeframe, ts DESC);

-- Snapshot of computed market state at scan time (Phase 2+).
CREATE TABLE IF NOT EXISTS market_states (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol        TEXT NOT NULL,
  ts            INTEGER NOT NULL,
  session       TEXT,
  current_price REAL,
  json_state    TEXT NOT NULL
);

-- Raw per-strategy candidates before the risk gate (Phase 3+).
CREATE TABLE IF NOT EXISTS strategy_outputs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol        TEXT NOT NULL,
  ts            INTEGER NOT NULL,
  strategy_name TEXT NOT NULL,
  direction     TEXT,
  grade         TEXT,
  json_output   TEXT NOT NULL
);

-- Final gated decisions (Phase 4+). decision is set by the risk gate ONLY.
CREATE TABLE IF NOT EXISTS signals (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol       TEXT NOT NULL,
  ts           INTEGER NOT NULL,
  session      TEXT,
  trading_day  TEXT,               -- YYYY-MM-DD of the 18:00-ET trading day
  decision     TEXT NOT NULL CHECK (decision IN ('LONG','SHORT','WAIT','REJECT')),
  setup        TEXT,
  grade        TEXT,
  entry_lo     REAL,
  entry_hi     REAL,
  stop         REAL,
  tp1          REAL,
  tp2          REAL,
  rr           REAL,
  reasons      TEXT,               -- JSON array
  warnings     TEXT,               -- JSON array
  invalidation TEXT,               -- JSON array
  json_signal  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signals_day
  ON signals (symbol, trading_day, session);

-- Human journal: what you actually did with each signal (Phase 5+).
CREATE TABLE IF NOT EXISTS trade_reviews (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id       INTEGER REFERENCES signals(id),
  symbol          TEXT NOT NULL,
  ts_open         INTEGER,
  ts_close        INTEGER,
  taken           INTEGER NOT NULL DEFAULT 0,
  result_r        REAL,
  max_favorable_r REAL,
  max_adverse_r   REAL,
  mistake_tags    TEXT,            -- JSON array
  notes           TEXT,
  screenshot_path TEXT,
  json_review     TEXT
);

CREATE TABLE IF NOT EXISTS daily_bias (
  trading_day TEXT NOT NULL,
  symbol      TEXT NOT NULL,
  bias        TEXT,
  notes       TEXT,
  PRIMARY KEY (trading_day, symbol)
);

CREATE TABLE IF NOT EXISTS mistakes (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  tag         TEXT NOT NULL,
  description TEXT,
  rule_update TEXT,
  applies_to  TEXT
);

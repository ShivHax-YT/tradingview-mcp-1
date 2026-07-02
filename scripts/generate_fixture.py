"""Generate deterministic synthetic 1m candles for tests/offline development.

Usage:
    python scripts/generate_fixture.py [out.csv] [--bars 480] [--symbol MNQ] [--start 2026-06-30T13:30:00Z]

Deterministic (seeded) random walk with plausible MNQ-ish prices. NOT market data.
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import datetime, timezone


def generate(symbol: str, start_ts: int, bars: int, seed: int = 42, start_price: float = 23200.0):
    rng = random.Random(seed)
    rows = []
    price = start_price
    for i in range(bars):
        ts = start_ts + i * 60
        drift = rng.uniform(-6, 6)
        o = round(price, 2)
        c = round(price + drift, 2)
        hi = round(max(o, c) + abs(rng.uniform(0, 4)), 2)
        lo = round(min(o, c) - abs(rng.uniform(0, 4)), 2)
        vol = round(rng.uniform(200, 3000))
        rows.append((symbol, "1m", ts, o, hi, lo, c, vol))
        price = c
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="tests/fixtures/mnq_1m_sample.csv")
    ap.add_argument("--bars", type=int, default=480)
    ap.add_argument("--symbol", default="MNQ")
    ap.add_argument("--start", default="2026-06-30T13:30:00Z")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    start_dt = datetime.fromisoformat(args.start.replace("Z", "+00:00")).astimezone(timezone.utc)
    rows = generate(args.symbol, int(start_dt.timestamp()), args.bars, args.seed)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "timeframe", "ts", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    print(f"wrote {len(rows)} synthetic 1m bars -> {args.out}")


if __name__ == "__main__":
    main()

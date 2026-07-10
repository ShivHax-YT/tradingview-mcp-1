from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path

from futures_copilot.weekly_review import (
    NARRATIVE_END,
    NARRATIVE_START,
    collect_weekly_review,
    render_weekly_review,
    weekly_review_path,
    write_weekly_review,
)


def _signal(store, day: date, n: int, *, decision: str = "LONG") -> int:
    checklist = [
        {"check": "golden_hour_allowed", "passed": n % 2 == 0, "detail": "fixture"},
        {"check": "trade_governor_clear", "passed": True, "detail": "fixture"},
    ]
    return store.save_signal(
        symbol="MNQ", ts=1_750_000_000 + n * 60, session="ny",
        trading_day=day.isoformat(), decision=decision, setup="trap", grade="A",
        entry_lo=100, entry_hi=101, stop=99, tp1=103, tp2=None, rr=2.0,
        reasons="[]", warnings="[]", invalidation="[]",
        json_signal=json.dumps({"candidate": {"direction": "long"}, "checklist": checklist}),
    )


def test_weekly_review_uses_seven_distinct_trading_days(config, store):
    as_of = date(2026, 7, 10)
    trading_days = []
    cursor = as_of
    while len(trading_days) < 8:
        if cursor.weekday() < 5:
            trading_days.append(cursor)
        cursor -= timedelta(days=1)
    ids = []
    for n, trading_day in enumerate(trading_days):
        ids.append(_signal(store, trading_day, n,
                           decision="REJECT" if n == 1 else "LONG"))
    for n, sid in enumerate(ids[:7]):
        store.add_trade_review(
            signal_id=sid, symbol="MNQ", taken=n != 2,
            result_r=(1.5 if n % 2 == 0 else -1.0) if n != 2 else None,
            mistake_tags=json.dumps(["chased|entry", "moved\nstop"] if n == 0 else ["moved_stop"]),
        )

    review = collect_weekly_review(store, config, as_of=as_of)

    assert len(review["days"]) == 7
    assert trading_days[-1].isoformat() not in review["days"]
    assert review["metrics"]["signals"] == 7
    assert review["metrics"]["rejected"] == 1
    assert review["filters"]["golden_hour_allowed"] == {
        "evaluated": 7, "passed": 4, "failed": 3,
    }
    assert review["filters"]["trade_governor_clear"]["passed"] == 7
    assert review["mistake_tags"]["moved_stop"] == 6


def test_weekly_markdown_has_taxonomy_matrix_and_manual_delimiter(config, store):
    as_of = date(2026, 7, 10)
    sid = _signal(store, as_of, 0)
    store.add_trade_review(signal_id=sid, symbol="MNQ", taken=True, result_r=2.0,
                           mistake_tags='["patient"]')
    review = collect_weekly_review(store, config, as_of=as_of)

    text = render_weekly_review(review, config)

    assert text.startswith("---\ntype: summary\nstatus: active\n")
    assert "content_hash:" in text and "review_after:" in text and "symbol: MNQ" in text
    assert "## Flash Summary Matrix" in text
    assert "| day | sym | sess | act | setup | dir | gr | rr | res_R | mist | PDH | PDL | ONH | ONL | drp |" in text
    assert "Trade Governor / Risk Filter Metrics" in text
    assert "| L | A |" in text
    assert "legacy signal timestamp" in text
    assert NARRATIVE_START in text and NARRATIVE_END in text
    assert text.rstrip().endswith(NARRATIVE_END)
    stored_hash = re.search(r"^content_hash: ([0-9a-f]+)$", text, re.MULTILINE).group(1)
    expected_hash = hashlib.sha256(
        json.dumps(review, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    assert stored_hash == expected_hash


def test_weekly_window_includes_observed_zero_signal_sessions(config, store):
    as_of = date(2026, 7, 10)
    days = [as_of - timedelta(days=n) for n in range(7)]
    for n, trading_day in enumerate(days):
        state = {
            "symbol": "MNQ", "trading_day": trading_day.isoformat(),
            "current_price": 100.0,
        }
        store.save_market_state(
            "MNQ", 1_750_100_000 + n * 60, "ny", 100.0, json.dumps(state),
        )
    _signal(store, as_of, 0)

    review = collect_weekly_review(store, config, as_of=as_of)

    assert len(review["days"]) == 7
    assert review["metrics"]["signals"] == 1


def test_weekly_writer_uses_iso_filename_and_preserves_manual_narrative(config, store, tmp_path):
    vault = tmp_path / "Trading Brain"
    vault.mkdir()
    config.vault.path = str(vault)
    as_of = date(2026, 12, 31)
    _signal(store, as_of, 0)

    path = write_weekly_review(store, config, as_of=as_of)
    assert path.name == "Weekly_Review_2026_W53.md"
    text = path.read_text(encoding="utf-8").replace(
        "Paste the manually generated narrative below. No API is called by this report.",
        "Keep this psychological insight.",
    )
    path.write_text(text, encoding="utf-8")

    write_weekly_review(store, config, as_of=as_of)

    assert "Keep this psychological insight." in path.read_text(encoding="utf-8")


def test_weekly_review_path_collapses_duplicate_terminal_vault(config, tmp_path):
    outer = tmp_path / "Trading Brain"
    inner = outer / "Trading Brain"
    inner.mkdir(parents=True)
    config.vault.path = str(inner)

    path = weekly_review_path(config, date(2026, 7, 10))

    assert path == outer / "Weekly_Review_2026_W28.md"


def test_weekly_review_path_terminates_for_filesystem_root(config):
    root = Path(config.root.anchor)
    config.vault.path = str(root)

    path = weekly_review_path(config, date(2026, 7, 10))

    assert path.parent == root

"""Similar-setup memory v1 — deterministic SQL + explicit band matching.

"Similar" means, in priority order:
  hard match : same setup_type, same direction, same symbol
  soft bands : same session; same sweep-level KIND (prior_day/asia/london/ny);
               grade within +/-1 band; RR within +/- rr_band
Each returned row carries its journal outcome (taken / result_r / mistakes)
so the packet can show "the last N times this happened, this is what you did".

Deliberately boring: no embeddings, no similarity scores you can't audit.
A row matches or it doesn't, and you can read why.
"""

from __future__ import annotations

import json
from typing import Any

from ..db.store import Store

GRADE_RANK = {"A": 0, "B": 1, "C": 2}


def _sweep_kind(level_name: str | None) -> str | None:
    if not level_name:
        return None
    for kind in ("prior_day", "asia", "london", "ny", "opening_range"):
        if level_name.startswith(kind):
            return kind
    return level_name


def similar_setups(
    store: Store, *,
    symbol: str,
    setup: str,
    direction: str,
    session: str | None = None,
    grade: str | None = None,
    rr: float | None = None,
    sweep_level: str | None = None,
    rr_band: float = 0.75,
    limit: int = 5,
    exclude_signal_id: int | None = None,
) -> list[dict[str, Any]]:
    """Past gated signals most like this one, newest first, with outcomes."""
    rows = store.conn.execute(
        """SELECT id, ts, session, trading_day, decision, setup, grade, rr, json_signal
           FROM signals
           WHERE symbol=? AND setup=? ORDER BY id DESC LIMIT 500""",
        (symbol, setup),
    ).fetchall()

    want_kind = _sweep_kind(sweep_level)
    out: list[dict[str, Any]] = []
    for sid, ts, sess, day, decision, setup_, grade_, rr_, js in rows:
        if exclude_signal_id is not None and sid == exclude_signal_id:
            continue
        try:
            cand = json.loads(js).get("candidate", {})
        except (TypeError, ValueError):
            cand = {}
        if cand.get("direction") != direction:
            continue
        if session is not None and sess != session:
            continue
        if grade is not None and grade_ in GRADE_RANK and grade in GRADE_RANK:
            if abs(GRADE_RANK[grade_] - GRADE_RANK[grade]) > 1:
                continue
        if rr is not None and rr_ is not None and abs(rr_ - rr) > rr_band:
            continue
        row_kind = _sweep_kind((cand.get("context") or {}).get("swept_level"))
        if want_kind is not None and row_kind is not None and row_kind != want_kind:
            continue
        review = store.review_for_signal(sid)
        out.append({
            "signal_id": sid, "ts": ts, "session": sess, "trading_day": day,
            "decision": decision, "grade": grade_, "rr": rr_,
            "swept_level": (cand.get("context") or {}).get("swept_level"),
            "outcome": review,
            "match": {
                "setup": setup_, "direction": direction, "session_match": sess == session,
                "sweep_kind": row_kind, "grade_band": grade_, "rr_delta": (
                    round(rr_ - rr, 2) if (rr is not None and rr_ is not None) else None),
            },
        })
        if len(out) >= limit:
            break
    return out

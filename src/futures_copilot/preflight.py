"""Desk Memory / Preflight (v0.3): deterministic journal memory.

Before a session, `copilot preflight` turns recent SQLite journal data
(trade reviews joined to signals, plus the mistake ledger) into a compact
JSON cache under data/session_memory/. The dashboard and Claude/Fable
packets READ that cache to remind the trader of repeated mistakes, recent
setup performance, and do-not-repeat rules before any trade is taken.

What this is:      counting, grouping, and plain-language reminders —
                   reproducible from the same rows every time.
What this is NOT:  model training, embeddings, or anything black-box.

Placement rules (enforced by construction):
- built at startup/offline only; the live scan/risk-gate path never imports
  or requires this module
- reads SQLite only — Obsidian belongs to `copilot prep`, the network to nobody
- the cache carries zero decision authority; the risk gate and the human decide
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .db.store import Store

PREFLIGHT_SCHEMA_VERSION = "copilot.preflight.v1"

WIN, LOSS, SCRATCH, UNRESOLVED = "win", "loss", "scratch", "unresolved"


def _outcome(review: dict) -> str | None:
    """Classify one review the same way the trade governor does: only TAKEN
    trades with a result count; skips are None; 0R is scratch."""
    if not review["taken"]:
        return None
    r = review["result_r"]
    if r is None:
        return UNRESOLVED
    return WIN if r > 0 else (LOSS if r < 0 else SCRATCH)


def _direction_of(review: dict) -> str | None:
    try:
        return (json.loads(review["json_signal"]).get("candidate") or {}).get("direction")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _tags_of(review: dict) -> list[str]:
    try:
        tags = json.loads(review["mistake_tags"] or "[]")
        return [str(t) for t in tags] if isinstance(tags, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def preflight_cache_path(config: Config, symbol: str, day: str) -> Path:
    return config.resolve(config.preflight.memory_dir) / f"{symbol}_{day}.json"


def build_preflight(store: Store, config: Config, symbol: str, day: str) -> dict[str, Any]:
    """Assemble the deterministic memory summary for one symbol + trading day."""
    p = config.preflight
    try:
        cutoff = (date.fromisoformat(day) - timedelta(days=p.lookback_days)).isoformat()
    except ValueError:
        cutoff = None

    reviews = store.reviews_with_signals(symbol, limit=p.lookback_reviews,
                                         since_day=cutoff)
    ledger = store.list_mistakes(limit=50)
    bias = store.get_daily_bias(day, symbol)

    # ── performance summary ─────────────────────────────────────────────────
    outcomes = [(_outcome(r), r) for r in reviews]
    taken = [r for o, r in outcomes if o is not None]
    wins = [r for o, r in outcomes if o == WIN]
    losses = [r for o, r in outcomes if o == LOSS]
    scratches = [r for o, r in outcomes if o == SCRATCH]
    with_result = [r for r in taken if r["result_r"] is not None]
    avg_r = (round(sum(r["result_r"] for r in with_result) / len(with_result), 2)
             if with_result else None)
    performance = {
        "total_reviewed": len(reviews),
        "taken": len(taken),
        "skipped": len(reviews) - len(taken),
        "wins": len(wins),
        "losses": len(losses),
        "scratches": len(scratches),
        "avg_result_r": avg_r,
    }

    # ── mistake tags: counted from review mistake_tags JSON ────────────────
    tag_counts = Counter(t for r in reviews for t in _tags_of(r))
    top_tags = [{"tag": t, "count": c}
                for t, c in tag_counts.most_common(p.top_mistakes)]

    # do-not-repeat rules from the ledger (newest first, non-empty rules only)
    rules = [{"tag": m["tag"], "rule": m["rule_update"]}
             for m in ledger if (m.get("rule_update") or "").strip()][:p.top_mistakes]

    # ── setup performance: grouped, only groups big enough to mean anything ─
    groups: dict[tuple, list[dict]] = {}
    for r in reviews:
        key = (r.get("setup") or "?", _direction_of(r) or "?", r.get("session") or "?")
        groups.setdefault(key, []).append(r)
    setup_performance = []
    for (setup, direction, session), rows in sorted(groups.items()):
        if len(rows) < p.min_samples_for_pattern:
            continue                          # small groups are noise, not patterns
        g_result = [r for r in rows if r["taken"] and r["result_r"] is not None]
        setup_performance.append({
            "setup": setup, "direction": direction, "session": session,
            "samples": len(rows),
            "taken": sum(1 for r in rows if r["taken"]),
            "wins": sum(1 for r in g_result if r["result_r"] > 0),
            "losses": sum(1 for r in g_result if r["result_r"] < 0),
            "avg_result_r": (round(sum(r["result_r"] for r in g_result) / len(g_result), 2)
                             if g_result else None),
            "grades_seen": sorted({r["grade"] for r in rows if r.get("grade")}),
        })

    # ── governor snapshot (same logic the gate uses) ────────────────────────
    governor = None
    try:
        from .gate import trade_governor_status
        clear, detail, counts = trade_governor_status(store, config, symbol, day)
        governor = {"clear": clear, "detail": detail, **counts}
    except Exception:
        pass                                  # snapshot is best-effort context

    # ── plain-language bullets (deterministic, formulaic) ───────────────────
    warnings: list[str] = []
    if not reviews:
        warnings.append("no journaled reviews in the lookback window yet — "
                        "reminders sharpen as you journal")
    rule_by_tag = {r["tag"]: r["rule"] for r in reversed(rules)}
    for t in top_tags:
        if t["count"] >= p.min_samples_for_pattern:
            line = f"'{t['tag']}' logged {t['count']}x in the last {len(reviews)} reviews"
            if t["tag"] in rule_by_tag:
                line += f" — rule: {rule_by_tag[t['tag']]}"
            warnings.append(line)
    for g in setup_performance:
        if g["avg_result_r"] is not None and g["avg_result_r"] < 0:
            warnings.append(
                f"{g['setup']} {g['direction']} in {g['session']} averages "
                f"{g['avg_result_r']:+.2f}R over {g['samples']} reviewed — respect the stats")
    if governor and not governor["clear"]:
        warnings.append(f"trade governor: {governor['detail']}")

    packet_bullets: list[str] = []
    if performance["total_reviewed"]:
        packet_bullets.append(
            f"last {performance['total_reviewed']} reviews: {performance['wins']}W / "
            f"{performance['losses']}L / {performance['scratches']} scratch, "
            f"{performance['skipped']} skipped"
            + (f", avg {performance['avg_result_r']:+.2f}R on taken" if avg_r is not None else ""))
    packet_bullets.extend(warnings)
    for r_ in rules:
        packet_bullets.append(f"do-not-repeat [{r_['tag']}]: {r_['rule']}")

    # staleness markers: the newest journal rows this cache has seen
    newest_review = store.list_trade_reviews(symbol, limit=1)
    newest_mistake = store.list_mistakes(limit=1)

    return {
        "preflight_schema": PREFLIGHT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": symbol,
        "trading_day": day,
        "lookback": {"reviews": p.lookback_reviews, "days": p.lookback_days,
                     "since_day": cutoff},
        "source_counts": {"reviews": len(reviews), "mistake_ledger": len(ledger)},
        "latest_review_id": newest_review[0]["id"] if newest_review else 0,
        "latest_mistake_id": newest_mistake[0]["id"] if newest_mistake else 0,
        "governor": governor,
        "daily_bias": bias,
        "top_mistake_tags": top_tags,
        "mistake_rules": rules,
        "performance": performance,
        "setup_performance": setup_performance,
        "warnings": warnings,
        "packet_bullets": packet_bullets,
        "note": ("Deterministic journal memory — statistics and reminders only. "
                 "No decision authority: the risk gate and the human decide."),
    }


def write_preflight(memory: dict[str, Any], config: Config) -> Path:
    out = preflight_cache_path(config, memory["symbol"], memory["trading_day"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(memory, indent=2, default=str), encoding="utf-8")
    return out


def load_preflight(config: Config, symbol: str, day: str | None) -> dict[str, Any] | None:
    """Cheap local JSON read; None when absent/unreadable. Callers (dashboard,
    packet) must always work without it."""
    if not day or not config.preflight.enabled:
        return None
    p = preflight_cache_path(config, symbol, day)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def preflight_is_stale(store: Store, memory: dict[str, Any]) -> bool:
    """Has anything been journaled since the cache was built? Two MAX(id)
    lookups — cheap enough for every dashboard refresh."""
    newest_review = store.list_trade_reviews(memory.get("symbol"), limit=1)
    newest_mistake = store.list_mistakes(limit=1)
    return (
        (newest_review[0]["id"] if newest_review else 0) > int(memory.get("latest_review_id") or 0)
        or (newest_mistake[0]["id"] if newest_mistake else 0) > int(memory.get("latest_mistake_id") or 0)
    )

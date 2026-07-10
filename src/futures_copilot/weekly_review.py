"""Deterministic seven-trading-day journal synthesis for Obsidian."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import Config
from .db.store import Store
from .errors import ReviewError
from .utils.roll_dates import ROLL_WINDOW_DAYS, get_next_roll_date

ET = ZoneInfo("America/New_York")
NARRATIVE_START = "<!-- CLAUDE_MANUAL_NARRATIVE_START -->"
NARRATIVE_END = "<!-- CLAUDE_MANUAL_NARRATIVE_END -->"


def _safe_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _tags(value: Any) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [_safe_cell(tag) for tag in parsed if _safe_cell(tag)]


def _checklist(value: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value or "{}")
        checks = parsed.get("checklist", [])
    except (AttributeError, json.JSONDecodeError, TypeError):
        return []
    return checks if isinstance(checks, list) else []


def collect_weekly_review(
    store: Store,
    config: Config,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Collect the newest seven distinct persisted trading days through as_of."""
    as_of = as_of or datetime.now(ET).date()
    observed_days = {
        row[0] for row in store.conn.execute(
            "SELECT DISTINCT trading_day FROM signals WHERE trading_day IS NOT NULL"
        ).fetchall()
        if row[0] <= as_of.isoformat()
    }
    state_by_day: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for symbol, ts, blob in store.conn.execute(
        "SELECT symbol, ts, json_state FROM market_states ORDER BY ts"
    ).fetchall():
        try:
            state = json.loads(blob)
            trading_day = state.get("trading_day")
            if trading_day and trading_day <= as_of.isoformat():
                observed_days.add(trading_day)
                state_by_day[(symbol, trading_day)].append((int(ts), state))
        except (TypeError, json.JSONDecodeError):
            continue
    days = sorted(observed_days, reverse=True)[:7]
    if days:
        placeholders = ",".join("?" for _ in days)
        query = f"""SELECT s.id, s.symbol, s.ts, s.trading_day, s.session, s.decision,
                            s.setup, s.grade, s.rr, s.json_signal,
                            tr.taken, tr.result_r, tr.mistake_tags
                     FROM signals s LEFT JOIN trade_reviews tr ON tr.signal_id = s.id
                     WHERE s.trading_day IN ({placeholders})
                     ORDER BY s.trading_day DESC, s.id DESC"""
        raw_rows = store.conn.execute(query, days).fetchall()
    else:
        raw_rows = []
    columns = [
        "id", "symbol", "ts", "trading_day", "session", "decision", "setup",
        "grade", "rr", "json_signal", "taken", "result_r", "mistake_tags",
    ]
    rows = [dict(zip(columns, row)) for row in raw_rows]

    for row in rows:
        try:
            candidate = json.loads(row["json_signal"] or "{}").get("candidate") or {}
        except (AttributeError, TypeError, json.JSONDecodeError):
            candidate = {}
        row["candidate_direction"] = candidate.get("direction")
        states = state_by_day.get((row["symbol"], row["trading_day"]), [])
        eligible = [(ts, state) for ts, state in states if ts >= int(row["ts"])]
        row["market_state"] = min(eligible, default=(0, {}), key=lambda item: item[0])[1]

    tag_counts = Counter(tag for row in rows for tag in _tags(row["mistake_tags"]))
    filters: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        for check in _checklist(row["json_signal"]):
            name = _safe_cell(check.get("check"))
            if not name:
                continue
            filters[name]["evaluated"] += 1
            filters[name]["passed" if bool(check.get("passed")) else "failed"] += 1

    reviewed = [row for row in rows if row["taken"] is not None]
    taken = [row for row in reviewed if bool(row["taken"])]
    resolved = [row for row in taken if row["result_r"] is not None]
    wins = sum(float(row["result_r"]) > 0 for row in resolved)
    losses = sum(float(row["result_r"]) < 0 for row in resolved)
    scratches = sum(float(row["result_r"]) == 0 for row in resolved)
    return {
        "as_of": as_of,
        "days": days,
        "rows": rows,
        "mistake_tags": dict(tag_counts.most_common()),
        "filters": {name: dict(counts) for name, counts in sorted(filters.items())},
        "metrics": {
            "signals": len(rows),
            "actionable": sum(row["decision"] in ("LONG", "SHORT") for row in rows),
            "rejected": sum(row["decision"] == "REJECT" for row in rows),
            "reviewed": len(reviewed),
            "taken": len(taken),
            "skipped": sum(not bool(row["taken"]) for row in reviewed),
            "unresolved": sum(row["result_r"] is None for row in taken),
            "wins": wins,
            "losses": losses,
            "scratches": scratches,
            "win_rate": wins / (wins + losses) if wins + losses else None,
            "avg_r": (
                sum(float(row["result_r"]) for row in resolved) / len(resolved)
                if resolved else None
            ),
        },
    }


def _fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None or value == "":
        return ""
    return f"{float(value):.{decimals}f}"


def render_weekly_review(
    review: dict[str, Any],
    config: Config,
    *,
    manual_narrative: str = "",
) -> str:
    as_of: date = review["as_of"]
    iso = as_of.isocalendar()
    metrics = review["metrics"]
    created = datetime.now(ET).isoformat(timespec="seconds")
    expires = as_of + timedelta(days=7)
    symbols = sorted({row["symbol"] for row in review["rows"]})
    hash_payload = json.dumps(review, sort_keys=True, separators=(",", ":"), default=str)
    content_hash = hashlib.sha256(hash_payload.encode("utf-8")).hexdigest()
    lines = [
        "---",
        "type: summary",
        "status: active",
        f"week: {iso.year}-W{iso.week:02d}",
        f"created: {created}",
        f"expires: {expires.isoformat()}",
        f"review_after: {expires.isoformat()}",
        f"symbol: {','.join(symbols) if symbols else 'all'}",
        f"content_hash: {content_hash}",
        'supersedes: ""',
        'superseded_by: ""',
        "authority: none",
        "source: local_sqlite_journal",
        "---",
        "",
        f"# Weekly Performance Review — {iso.year} W{iso.week:02d}",
        "",
        f"Observed trading-day window (up to seven sessions): "
        f"{', '.join(review['days']) if review['days'] else 'no persisted sessions'}.",
        "WAIT-only scans are not persisted; counts below cover unique persisted candidate signals.",
        "",
        "## Performance Summary",
        "",
        "| signals | actionable | rejected | reviewed | taken | skipped | unresolved | W | L | scratch | win rate | avg R |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| {metrics['signals']} | {metrics['actionable']} | {metrics['rejected']} | "
        f"{metrics['reviewed']} | {metrics['taken']} | {metrics['skipped']} | "
        f"{metrics['unresolved']} | {metrics['wins']} | {metrics['losses']} | "
        f"{metrics['scratches']} | "
        f"{_fmt_num(metrics['win_rate'] * 100) + '%' if metrics['win_rate'] is not None else 'n/a'} | "
        f"{_fmt_num(metrics['avg_r']) if metrics['avg_r'] is not None else 'n/a'} |",
        "",
        "## Flash Summary Matrix",
        "",
        "| day | sym | sess | act | setup | dir | gr | rr | res_R | mist | PDH | PDL | ONH | ONL | drp |",
        "|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in review["rows"]:
        state = row.get("market_state") or {}
        prior = state.get("prior_day") or {}
        overnight = (state.get("session_levels") or {}).get("asia") or {}
        tags = _tags(row.get("mistake_tags"))
        direction = "L" if row.get("candidate_direction") == "long" else (
            "S" if row.get("candidate_direction") == "short" else ""
        )
        lines.append(
            f"| {row['trading_day'][5:]} | {_safe_cell(row['symbol'])} | {_safe_cell(row['session'])} | "
            f"{_safe_cell(row['decision'])} | {_safe_cell(row['setup'])} | {direction} | "
            f"{_safe_cell(row['grade'])} | {_fmt_num(row['rr'])} | {_fmt_num(row['result_r'])} | "
            f"{len(tags)} | {_fmt_num(prior.get('high'))} | {_fmt_num(prior.get('low'))} | "
            f"{_fmt_num(overnight.get('high'))} | {_fmt_num(overnight.get('low'))} | "
            f"{_fmt_num(state.get('day_range_position'), 4)} |"
        )
    lines += [
        "",
        "Market-state columns use the earliest same-trading-day snapshot at or after each "
        "legacy signal timestamp; unavailable values remain blank.",
    ]
    r = config.risk
    next_roll = get_next_roll_date(as_of)
    roll_start, roll_end = next_roll - timedelta(days=ROLL_WINDOW_DAYS), next_roll + timedelta(days=ROLL_WINDOW_DAYS)
    blackouts = ", ".join(nb.start for nb in r.news_blackouts) or "none configured"
    lines += [
        "",
        f"RULES_ACTIVE: min_rr={r.min_rr}; expiry={r.signal_expiry_candles}x5m; "
        f"sessions={','.join(r.allowed_sessions)}; golden={r.golden_hour[0]}-{r.golden_hour[1]}; "
        f"stop_on_first_win={str(r.stop_on_first_win).lower()}; max_losses={r.stop_after_losses}",
        f"BOUNDARIES: next_roll_window={roll_start.isoformat()}..{roll_end.isoformat()}; "
        f"news_blackouts={blackouts}",
        "MISTAKE_TAGS_OPEN: " + (
            "; ".join(f"{tag} x{count}" for tag, count in review["mistake_tags"].items())
            or "none"
        ),
        "",
        "## Trade Governor / Risk Filter Metrics",
        "",
        "| filter | evaluated | passed | failed |",
        "|---|---:|---:|---:|",
    ]
    for name, counts in review["filters"].items():
        lines.append(
            f"| {_safe_cell(name)} | {counts.get('evaluated', 0)} | "
            f"{counts.get('passed', 0)} | {counts.get('failed', 0)} |"
        )
    lines += [
        "",
        "---",
        NARRATIVE_START,
        "## Manual Claude Strategic / Psychological Narrative",
        "",
        manual_narrative.strip() or "Paste the manually generated narrative below. No API is called by this report.",
        NARRATIVE_END,
        "",
    ]
    return "\n".join(lines)


def weekly_review_path(config: Config, as_of: date) -> Path:
    vault = Path(config.vault.path) if config.vault.path else config.root.parent / "Trading Brain"
    if not vault.is_dir():
        raise ReviewError(
            f"Obsidian vault does not exist: {vault}",
            hint="set vault.path in config.yaml to the Trading Brain vault directory",
        )
    iso = as_of.isocalendar()
    return vault / f"Weekly_Review_{iso.year}_W{iso.week:02d}.md"


def _existing_narrative(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if NARRATIVE_START not in text or NARRATIVE_END not in text:
        return ""
    body = text.split(NARRATIVE_START, 1)[1].split(NARRATIVE_END, 1)[0]
    heading = "## Manual Claude Strategic / Psychological Narrative"
    return body.replace(heading, "", 1).strip()


def write_weekly_review(store: Store, config: Config, *, as_of: date | None = None) -> Path:
    as_of = as_of or datetime.now(ET).date()
    path = weekly_review_path(config, as_of)
    review = collect_weekly_review(store, config, as_of=as_of)
    text = render_weekly_review(review, config, manual_narrative=_existing_narrative(path))
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ReviewError(f"could not write weekly review: {path}", hint=str(exc)) from exc
    return path

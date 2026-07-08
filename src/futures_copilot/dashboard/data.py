"""Pure data-access helpers for the dashboard (import-safe, no streamlit).

Everything here is read/journal only. There is no order, alert, or execution
concept anywhere in this package — by design, permanently.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..config import Config, load_config
from ..db.store import Store
from ..features.sessions import to_et
from ..memory import similar_setups
from ..packet.writer import _signal_is_current
from ..strategies.liquidity_trap import TF_SECONDS


def find_config(start: Path | None = None) -> Path:
    """Locate config.yaml from cwd or repo root (streamlit runs from anywhere)."""
    here = (start or Path.cwd()).resolve()
    for base in (here, *here.parents):
        cand = base / "config.yaml"
        if cand.exists():
            return cand
    # fall back to the package's repo layout: src/futures_copilot/dashboard -> repo root
    pkg_root = Path(__file__).resolve().parents[3]
    cand = pkg_root / "config.yaml"
    if cand.exists():
        return cand
    raise FileNotFoundError("config.yaml not found — run from the project root")


def open_store(config: Config) -> Store:
    return Store(config.db_file)


def fmt_ts(ts: int | None) -> str:
    if ts is None:
        return "—"
    return to_et(int(ts)).strftime("%b %d · %H:%M ET")


def freshness(state_row: dict | None) -> tuple[str, int | None]:
    """('live'|'stale'|'none', minutes_old)."""
    if not state_row:
        return "none", None
    age_min = int((time.time() - int(state_row["ts"])) // 60)
    return ("live" if age_min <= 5 else "stale"), age_min


def _candidate_is_current(row: dict[str, Any], state: dict[str, Any] | None, config: Config) -> bool:
    """Return whether a persisted strategy output still belongs to the latest scan horizon."""
    if not state:
        return False
    try:
        cand = json.loads(row["json_output"])
        confirmed = int(cand["confirmed_close_ts"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    horizon = int(state.get("as_of_close_ts") or state.get("ts") or 0)
    tf_s = TF_SECONDS.get(cand.get("detection_timeframe", "5m"), 300)
    return horizon <= confirmed + config.risk.signal_expiry_candles * tf_s


def load_overview(store: Store, config: Config | str, symbol: str | None = None) -> dict[str, Any]:
    if symbol is None:
        symbol = str(config)
        config = load_config(find_config())
    ms_row = store.latest_market_state(symbol)
    state = json.loads(ms_row["json_state"]) if ms_row else None
    sig_rows = store.latest_signals(symbol, limit=12)
    current_sigs = [r for r in sig_rows if state and _signal_is_current(r, state, config)]
    latest_sig = current_sigs[0] if current_sigs else None
    candidate_rows = store.latest_strategy_outputs(symbol, limit=8)
    current_candidates = [r for r in candidate_rows if _candidate_is_current(r, state, config)]
    return {
        "state_row": ms_row,
        "state": state,
        "signals": sig_rows,
        "latest_signal": latest_sig,
        "candidates": current_candidates,
        "coverage": [c for c in store.coverage() if c["symbol"] == symbol],
        "mistakes": store.list_mistakes(limit=12),
        "reviews": store.list_trade_reviews(symbol, limit=12),
        "bias": (store.get_daily_bias(state["trading_day"], symbol) if state else None),
    }


def desk_mode_status(store: Store, config: Config, state: dict | None) -> dict[str, Any]:
    """Desk Mode panel data: golden hour, trade governor, prep cache, packet
    readiness inputs. Purely local reads (SQLite + one file-exists check) —
    never the vault contents, never the network, never Claude. Judged at the
    market state's as_of_close_ts, same clock as the risk gate."""
    from ..gate import golden_hour_status, trade_governor_status
    from ..prep import prep_cache_path

    if not state:
        return {"golden_hour": None, "governor": None, "prep": None}
    symbol = state.get("symbol", "MNQ")
    day = state.get("trading_day")
    horizon = int(state.get("as_of_close_ts") or state.get("ts") or 0)
    within, gh_detail = golden_hour_status(config, horizon)
    clear, gov_detail, counts = trade_governor_status(store, config, symbol, day)
    prep_path = prep_cache_path(config, symbol, day) if day else None
    return {
        "golden_hour": {
            "enforced": config.risk.enforce_golden_hour,
            "within": within,
            "window": list(config.risk.golden_hour),
            "detail": gh_detail,
        },
        "governor": {"clear": clear, "detail": gov_detail, **counts},
        "prep": {
            "exists": bool(prep_path and prep_path.exists()),
            "path": str(prep_path) if prep_path else None,
        },
    }


def preflight_view(store: Store, config: Config, symbol: str,
                   state: dict | None) -> dict[str, Any]:
    """Desk Reminders panel data. Reads ONLY the local preflight cache plus two
    cheap MAX(id) lookups for staleness — no summarization per refresh, no
    TradingView, no Obsidian, no Claude.
    status: 'cached' | 'stale' | 'missing' | 'disabled'."""
    from ..preflight import load_preflight, preflight_is_stale

    if not config.preflight.enabled:
        return {"status": "disabled", "cache": None}
    day = (state or {}).get("trading_day")
    cache = load_preflight(config, symbol, day)
    if cache is None:
        return {"status": "missing", "cache": None}
    status = "stale" if preflight_is_stale(store, cache) else "cached"
    return {"status": status, "cache": cache}


def rebuild_preflight(store: Store, config: Config, symbol: str, day: str) -> str:
    """Dashboard refresh button: rebuild the LOCAL cache only. SQLite in, JSON
    out — touches nothing else, decides nothing."""
    from ..preflight import build_preflight, write_preflight

    path = write_preflight(build_preflight(store, config, symbol, day), config)
    return str(path)


def checklist_item(gv: dict, name: str) -> dict | None:
    """Find one named check in a gate view's checklist (None if not gated yet)."""
    for c in gv.get("checklist") or []:
        if c.get("check") == name:
            return c
    return None


def gate_view(sig_row: dict | None) -> dict[str, Any]:
    """Signal row -> {decision, checklist, reasons, warnings, invalidation, candidate}."""
    if not sig_row:
        return {"decision": "WAIT", "checklist": [], "reasons": [], "warnings": [],
                "invalidation": [], "candidate": None, "signal_id": None}
    js = json.loads(sig_row.get("json_signal") or "{}")
    return {
        "decision": sig_row["decision"],
        "signal_id": sig_row["id"],
        "checklist": js.get("checklist", []),
        "reasons": json.loads(sig_row.get("reasons") or "[]"),
        "warnings": json.loads(sig_row.get("warnings") or "[]"),
        "invalidation": json.loads(sig_row.get("invalidation") or "[]"),
        "candidate": js.get("candidate"),
    }


def similar_for_signal(store: Store, sig_row: dict) -> list[dict]:
    gv = gate_view(sig_row)
    cand = gv["candidate"] or {}
    if not cand:
        return []
    return similar_setups(
        store, symbol=sig_row["symbol"], setup=sig_row.get("setup") or cand.get("setup_type", ""),
        direction=cand.get("direction", ""), session=sig_row.get("session"),
        grade=sig_row.get("grade"), rr=sig_row.get("rr"),
        sweep_level=(cand.get("context") or {}).get("swept_level"),
        exclude_signal_id=sig_row["id"],
    )


def levels_ladder(state: dict) -> list[tuple[str, float, str]]:
    """[(name, price, source_tf)] sorted top-down like a price ladder."""
    out: list[tuple[str, float, str]] = []
    src = state.get("level_sources") or {}
    pd_ = state.get("prior_day")
    if pd_:
        out.append(("Prior day high", pd_["high"], src.get("prior_day") or ""))
        out.append(("Prior day low", pd_["low"], src.get("prior_day") or ""))
    for name, lp in (state.get("session_levels") or {}).items():
        if lp:
            out.append((f"{name.capitalize()} high", lp["high"], src.get(name) or ""))
            out.append((f"{name.capitalize()} low", lp["low"], src.get(name) or ""))
    orng = state.get("opening_range")
    if orng:
        out.append(("Opening range high", orng["high"], "1m"))
        out.append(("Opening range low", orng["low"], "1m"))
    if state.get("vwap") is not None:
        out.append(("VWAP", round(state["vwap"], 2), "1m"))
    return sorted(out, key=lambda r: -float(r[1]))

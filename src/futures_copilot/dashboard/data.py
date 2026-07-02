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


def load_overview(store: Store, symbol: str) -> dict[str, Any]:
    ms_row = store.latest_market_state(symbol)
    state = json.loads(ms_row["json_state"]) if ms_row else None
    sig_rows = store.latest_signals(symbol, limit=12)
    latest_sig = sig_rows[0] if sig_rows else None
    return {
        "state_row": ms_row,
        "state": state,
        "signals": sig_rows,
        "latest_signal": latest_sig,
        "candidates": store.latest_strategy_outputs(symbol, limit=8),
        "coverage": [c for c in store.coverage() if c["symbol"] == symbol],
        "mistakes": store.list_mistakes(limit=12),
        "reviews": store.list_trade_reviews(symbol, limit=12),
        "bias": (store.get_daily_bias(state["trading_day"], symbol) if state else None),
    }


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

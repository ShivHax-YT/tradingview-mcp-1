"""Obsidian session-prep cache (Desk Mode v0.2).

`copilot prep --symbol MNQ --day YYYY-MM-DD` reads a SMALL, HARDCODED
allowlist of vault notes once, before the session, and writes a compact JSON
cache under data/session_prep/. The packet and dashboard may READ that cache
if it exists; nothing in the live loop ever touches the vault.

Latency rule this module enforces by construction:
- the vault is read only by the explicit `prep` command (never per tick)
- the allowlist is fixed in code (no vault crawling, no glob, no recursion)
- scan / risk gate / packet / dashboard all work when no cache exists
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .errors import ConfigError

PREP_SCHEMA_VERSION = "copilot.session_prep.v1"

# The ONLY vault files prep may read, relative to the vault root.
VAULT_ALLOWLIST: tuple[str, ...] = (
    "00 Command Center/Trading Bot Command Center.md",
    "02 Bot Plans/Modular Paper Trading Copilot.md",
    "06 Strategies/MTF Session Liquidity Trap Scalper.md",
    "05 Concepts/Risk Management.md",
    "02 Bot Plans/Open Questions.md",
)


def prep_cache_path(config: Config, symbol: str, day: str) -> Path:
    return config.resolve(config.vault.session_prep_dir) / f"{symbol}_{day}.json"


def _front_matter(text: str) -> dict[str, str]:
    """Cheap front-matter scalars (status/expires/...). No YAML dependency."""
    text = text.lstrip("\ufeff")  # Windows/Obsidian UTF-8 BOM is not content.
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    for line in text[3:end].splitlines():
        key, sep, val = line.partition(":")
        if sep:
            out[key.strip().lower()] = val.strip().strip('"').strip("'")
    return out


def _lifecycle_snapshot(text: str) -> dict[str, str]:
    """Lifecycle scalars captured before a note can be truncated for caching."""
    fm = _front_matter(text)
    return {
        "status": (fm.get("status") or "active").lower(),
        "expires": fm.get("expires") or "",
    }


def _lifecycle_is_ingestible(lifecycle: dict[str, str], today: str) -> tuple[bool, str]:
    """(ok, reason) for a captured status/expiry lifecycle snapshot."""
    status = (lifecycle.get("status") or "active").lower()
    if status != "active":
        return False, f"status:{status}"
    expires = lifecycle.get("expires") or ""
    if expires and expires < today:            # ISO dates compare lexically
        return False, f"expired:{expires}"
    return True, ""


def _note_is_ingestible(text: str, today: str) -> tuple[bool, str]:
    """Deprecated/superseded/expired notes must NOT reach Claude.
    Notes without front matter default to active — additive, non-breaking."""
    return _lifecycle_is_ingestible(_lifecycle_snapshot(text), today)


def build_session_prep(config: Config, symbol: str, day: str,
                       vault_dir: str | Path | None = None) -> dict[str, Any]:
    """Read the allowlisted notes and assemble the compact cache dict."""
    root = Path(vault_dir) if vault_dir else (
        Path(config.vault.path) if config.vault.path else None)
    if root is None:
        raise ConfigError(
            "no vault path configured",
            hint="set vault.path in config.yaml or pass --vault <dir> to `copilot prep`",
        )
    if not root.exists():
        raise ConfigError(f"vault path does not exist: {root}",
                          hint="check vault.path in config.yaml")

    max_chars = int(config.vault.max_chars_per_note)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    notes: list[dict[str, Any]] = []
    missing: list[str] = []
    excluded: list[dict[str, str]] = []
    for rel in VAULT_ALLOWLIST:
        p = root / rel
        if not p.exists():
            missing.append(rel)
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        lifecycle = _lifecycle_snapshot(text)
        ok, why = _lifecycle_is_ingestible(lifecycle, today)
        if not ok:
            excluded.append({"path": rel, "reason": why})
            continue
        truncated = len(text) > max_chars
        notes.append({
            "path": rel,
            "title": p.stem,
            "chars": len(text),
            "truncated": truncated,
            "content": text[:max_chars],
            # Do not re-parse possibly truncated content at packet-read time.
            "lifecycle": lifecycle,
        })
    return {
        "prep_schema": PREP_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": symbol,
        "trading_day": day,
        "vault_root": str(root),
        "allowlist": list(VAULT_ALLOWLIST),
        "missing": missing,
        "excluded": excluded,
        "notes": notes,
        "note": ("Vault context for Claude/Fable explanation only. It carries no "
                 "decision authority — the risk gate and the human decide."),
    }


def write_session_prep(prep: dict[str, Any], config: Config) -> Path:
    out = prep_cache_path(config, prep["symbol"], prep["trading_day"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(prep, indent=2, default=str), encoding="utf-8")
    return out


def load_session_prep(config: Config, symbol: str, day: str | None) -> dict[str, Any] | None:
    """Read and re-check a cache without touching the vault in the live loop.

    Legacy cache notes without a pre-truncation lifecycle snapshot fail closed:
    run ``copilot prep`` to rebuild them. This keeps an expired cached note out
    of packet context while preserving the no-live-vault-read boundary.
    """
    if not day:
        return None
    p = prep_cache_path(config, symbol, day)
    if not p.exists():
        return None
    try:
        prep = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(prep, dict):
        return None

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    notes = prep.get("notes") or []
    excluded = list(prep.get("excluded") or [])
    active_notes: list[dict[str, Any]] = []
    for note in notes:
        if not isinstance(note, dict):
            excluded.append({"path": "", "reason": "invalid_cached_note"})
            continue
        lifecycle = note.get("lifecycle")
        if not isinstance(lifecycle, dict):
            excluded.append({
                "path": str(note.get("path") or ""),
                "reason": "lifecycle_snapshot_missing",
            })
            continue
        normalized = {
            "status": str(lifecycle.get("status") or "active"),
            "expires": str(lifecycle.get("expires") or ""),
        }
        ok, why = _lifecycle_is_ingestible(normalized, today)
        if not ok:
            excluded.append({"path": str(note.get("path") or ""), "reason": why})
            continue
        note["lifecycle"] = normalized
        active_notes.append(note)
    prep["notes"] = active_notes
    prep["excluded"] = excluded
    return prep

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
    notes: list[dict[str, Any]] = []
    missing: list[str] = []
    for rel in VAULT_ALLOWLIST:
        p = root / rel
        if not p.exists():
            missing.append(rel)
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > max_chars
        notes.append({
            "path": rel,
            "title": p.stem,
            "chars": len(text),
            "truncated": truncated,
            "content": text[:max_chars],
        })
    return {
        "prep_schema": PREP_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": symbol,
        "trading_day": day,
        "vault_root": str(root),
        "allowlist": list(VAULT_ALLOWLIST),
        "missing": missing,
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
    """Read a previously written cache. Cheap local JSON read; returns None when
    absent or unreadable — callers must always work without it."""
    if not day:
        return None
    p = prep_cache_path(config, symbol, day)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

"""Strict symbol helpers for CLI/user input.

The app stores and configures canonical roots (MNQ, MES). User input may carry
front-month suffixes such as MNQ1! or MES-2026; normalize before reaching the
data and strategy layers.
"""

from __future__ import annotations

import re


SUPPORTED_SYMBOL_ROOTS = {"MNQ", "MES"}


def normalize_symbol(symbol: str) -> str:
    """Return the canonical futures root from a user/chart symbol string."""
    root = re.sub(r"[0-9!\-]+$", "", symbol.strip()).upper()
    return root.split(":")[-1]


def validate_symbol(symbol: str) -> bool:
    """Return whether a user-provided symbol resolves to a supported root."""
    return normalize_symbol(symbol) in SUPPORTED_SYMBOL_ROOTS


def resolve_symbol(symbol: str) -> str:
    """Validate a symbol and return the original value if supported."""
    if not validate_symbol(symbol):
        supported = ", ".join(sorted(SUPPORTED_SYMBOL_ROOTS))
        raise ValueError(
            f"unsupported symbol {symbol!r}; use one of {supported} "
            "or a front-month form such as MNQ1! / MES1!"
        )
    return symbol

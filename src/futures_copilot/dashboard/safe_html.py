"""HTML escaping for dashboard rendering.

Every dynamic value interpolated into an `st.markdown(..., unsafe_allow_html=True)`
string MUST pass through `h()` first. Journal notes, mistake tags/descriptions/
rules, preflight reminders, and anything round-tripped through JSON blobs are
user-controlled text and must never be rendered as raw HTML.

Rule of thumb: if it isn't a literal in this file's caller, escape it.
"""

from __future__ import annotations

from html import escape
from typing import Any


def h(value: Any) -> str:
    """Escape a dynamic value for safe interpolation inside HTML markup.

    None renders as the empty string. Everything else is stringified and
    HTML-escaped (including quotes, so values are safe inside attributes).
    """
    return escape("" if value is None else str(value), quote=True)

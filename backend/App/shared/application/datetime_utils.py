"""Datetime helpers shared across domains.

Centralises the ``datetime.now(timezone.utc)`` + ``.isoformat()`` pattern that
was previously duplicated in six+ files (task entities, trace emitter, doc
fetch, code analysis, etc.). Keeping it in one place makes it trivial to swap
the clock source in tests or add precision/format tweaks globally.
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["utc_now", "utc_now_iso"]


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 format (``YYYY-MM-DDTHH:MM:SS.ffffff+00:00``)."""
    return utc_now().isoformat()

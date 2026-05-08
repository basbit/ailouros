from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_PATH = _PROJECT_ROOT / "config" / "heuristic_tables.json"


def _path() -> Path:
    raw = os.getenv("SWARM_HEURISTIC_TABLES_PATH", "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        return candidate if candidate.is_absolute() else _PROJECT_ROOT / candidate
    return _DEFAULT_PATH


@lru_cache(maxsize=1)
def _load_tables() -> dict[str, Any]:
    path = _path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("heuristic_tables: %s", exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def stub_patterns() -> tuple[re.Pattern[str], ...]:
    tables = _load_tables()
    raw = tables.get("stub_patterns") if isinstance(tables, dict) else None
    if not isinstance(raw, list):
        return ()
    compiled: list[re.Pattern[str]] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry:
            continue
        try:
            compiled.append(re.compile(entry, re.IGNORECASE | re.MULTILINE))
        except re.error as exc:
            logger.debug("heuristic_tables: bad stub pattern %r: %s", entry, exc)
    return tuple(compiled)


def fake_tool_call_patterns() -> tuple[re.Pattern[str], ...]:
    tables = _load_tables()
    raw = tables.get("fake_tool_call_patterns") if isinstance(tables, dict) else None
    if not isinstance(raw, list):
        return ()
    compiled: list[re.Pattern[str]] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry:
            continue
        try:
            compiled.append(re.compile(entry))
        except re.error as exc:
            logger.debug("heuristic_tables: bad tool-call pattern %r: %s", entry, exc)
    return tuple(compiled)


def patch_marker_patterns() -> tuple[re.Pattern[str], ...]:
    tables = _load_tables()
    raw = tables.get("patch_marker_patterns") if isinstance(tables, dict) else None
    if not isinstance(raw, list):
        return ()
    compiled: list[re.Pattern[str]] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry:
            continue
        try:
            compiled.append(re.compile(entry, re.MULTILINE))
        except re.error as exc:
            logger.debug("heuristic_tables: bad patch marker %r: %s", entry, exc)
    return tuple(compiled)


def reload_tables() -> None:
    _load_tables.cache_clear()

"""Small helpers for JSON file I/O.

Every other integration used to repeat the same ``.read_text(encoding="utf-8")
+ json.loads`` and ``json.dumps(..., ensure_ascii=False) + .write_text(...,
encoding="utf-8")`` pattern (memory notes, user settings, pattern memory,
failure memory, agent registry …).

These helpers unify UTF-8 handling and default formatting so callers stop
re-inventing it and we can swap the encoder (e.g. for pretty-printing in dev)
in one place.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["read_json_file", "write_json_file"]


def read_json_file(path: str | Path) -> Any:
    """Read and parse a UTF-8 JSON file. Raises ``FileNotFoundError`` / ``JSONDecodeError``."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json_file(
    path: str | Path,
    data: Any,
    *,
    indent: int | None = 2,
    ensure_ascii: bool = False,
    trailing_newline: bool = True,
    sort_keys: bool = False,
) -> None:
    """Serialise ``data`` as UTF-8 JSON and atomically overwrite ``path``.

    Writes ``ensure_ascii=False`` by default so non-ASCII text is preserved.
    Adds a trailing newline unless ``trailing_newline=False``.
    """
    payload = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii, sort_keys=sort_keys)
    if trailing_newline and not payload.endswith("\n"):
        payload += "\n"
    Path(path).write_text(payload, encoding="utf-8")

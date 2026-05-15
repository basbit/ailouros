
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["read_json_file", "write_json_file"]


def read_json_file(path: str | Path) -> Any:
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
    payload = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii, sort_keys=sort_keys)
    if trailing_newline and not payload.endswith("\n"):
        payload += "\n"
    Path(path).write_text(payload, encoding="utf-8")

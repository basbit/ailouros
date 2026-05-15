from __future__ import annotations

from typing import Any, Mapping


def resolved_workspace_root(state: Mapping[str, Any]) -> str:
    resolved = str(state.get("workspace_root_resolved") or "").strip()
    if resolved:
        return resolved
    return str(state.get("workspace_root") or "").strip()


__all__ = ["resolved_workspace_root"]

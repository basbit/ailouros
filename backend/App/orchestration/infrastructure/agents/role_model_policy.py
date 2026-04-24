
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


def _policy_path() -> Path:
    project_root = (
        Path(os.environ.get("SWARM_PROJECT_ROOT", "")).resolve()
        if os.environ.get("SWARM_PROJECT_ROOT")
        else Path(__file__).resolve().parents[5]
    )
    return project_root / "config" / "agent_role_model_policy.json"


@lru_cache(maxsize=1)
def load_role_model_policy() -> dict[str, Any]:
    path = _policy_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"role model policy must be an object: {path}")
    return data


def planning_roles() -> set[str]:
    raw = load_role_model_policy().get("planning_roles") or []
    return {str(item).strip().upper() for item in raw if str(item).strip()}


def planning_model_roles() -> set[str]:
    roles = planning_roles()
    excluded = {
        str(item).strip().upper()
        for item in (load_role_model_policy().get("planning_model_roles_exclude") or [])
        if str(item).strip()
    }
    return roles - excluded


def build_roles() -> set[str]:
    raw = load_role_model_policy().get("build_roles") or []
    return {str(item).strip().upper() for item in raw if str(item).strip()}

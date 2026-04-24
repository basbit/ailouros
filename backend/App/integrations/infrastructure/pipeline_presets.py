from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_PATH = _PROJECT_ROOT / "config" / "pipeline_presets.json"


def _path() -> Path:
    raw = os.getenv("SWARM_PIPELINE_PRESETS_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else _PROJECT_ROOT / p
    return _DEFAULT_PATH


def load_presets() -> dict[str, Any]:
    path = _path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("pipeline_presets: %s", exc)
        return {}
    if not isinstance(data, dict):
        return {}
    presets = data.get("presets")
    return presets if isinstance(presets, dict) else {}


def _validate_preset_step_ids(preset_name: str, steps: list[str]) -> None:
    from backend.App.orchestration.application.nodes.custom import parse_custom_role_slug
    from backend.App.orchestration.application.routing.step_registry import PIPELINE_STEP_REGISTRY

    unknown = [
        s
        for s in steps
        if s not in PIPELINE_STEP_REGISTRY and parse_custom_role_slug(s) is None
    ]
    if unknown:
        raise ValueError(
            f"Pipeline preset {preset_name!r} contains unknown step ids: {unknown}"
        )


def resolve_preset(name: Optional[str]) -> Optional[list[str]]:
    if not name or not str(name).strip():
        return None
    key = str(name).strip()
    if key == "spec_only":
        key = "planning_loop"
    presets = load_presets()
    entry = presets.get(key)
    if entry is None:
        return None
    if isinstance(entry, list):
        steps = [str(x).strip() for x in entry if str(x).strip()]
        _validate_preset_step_ids(key, steps)
        return steps
    return None

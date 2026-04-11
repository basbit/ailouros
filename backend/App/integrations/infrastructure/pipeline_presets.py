"""Именованные пресеты pipeline_steps (JSON).

Файл: ``config/pipeline_presets.json`` или ``SWARM_PIPELINE_PRESETS_PATH``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]  # backend/App/integrations/infrastructure -> project root
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


def resolve_preset(name: Optional[str]) -> Optional[list[str]]:
    if not name or not str(name).strip():
        return None
    presets = load_presets()
    entry = presets.get(str(name).strip())
    if entry is None:
        return None
    if isinstance(entry, list):
        return [str(x).strip() for x in entry if str(x).strip()]
    return None

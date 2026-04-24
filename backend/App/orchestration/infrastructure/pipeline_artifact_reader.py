
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.App.paths import artifacts_root

logger = logging.getLogger(__name__)


def _default_artifacts_dir() -> Path:
    return artifacts_root()


def load_partial_pipeline_state(
    task_id: str,
    artifacts_dir: Path | None = None,
) -> dict[str, Any]:
    root = artifacts_dir if artifacts_dir is not None else _default_artifacts_dir()
    pipeline_json: Path = root / task_id / "pipeline.json"
    if not pipeline_json.is_file():
        logger.info(
            "load_partial_pipeline_state: no pipeline.json for task_id=%s", task_id
        )
        return {}
    try:
        data: dict[str, Any] = json.loads(pipeline_json.read_text())
        return data.get("partial_state") or {}
    except Exception as exc:
        logger.warning(
            "load_partial_pipeline_state: failed to read pipeline.json task_id=%s: %s",
            task_id,
            exc,
        )
        return {}


def load_failed_step(
    task_id: str,
    artifacts_dir: Path | None = None,
) -> str:
    root = artifacts_dir if artifacts_dir is not None else _default_artifacts_dir()
    pipeline_json: Path = root / task_id / "pipeline.json"
    if not pipeline_json.is_file():
        return ""
    try:
        data: dict[str, Any] = json.loads(pipeline_json.read_text())
        return str(data.get("failed_step") or "")
    except Exception:
        return ""

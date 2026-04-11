"""Infrastructure: reads pipeline run artifacts from disk."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _default_artifacts_dir() -> Path:
    """Return the default artifacts root, resolved from SWARM_ARTIFACTS_DIR env var."""
    return Path(os.getenv("SWARM_ARTIFACTS_DIR", "var/artifacts")).resolve()


def load_partial_pipeline_state(
    task_id: str,
    artifacts_dir: Path | None = None,
) -> dict[str, Any]:
    """Read partial pipeline state from pipeline.json for a given task_id.

    Returns empty dict if not found or unreadable.

    Args:
        task_id: The task identifier whose pipeline.json to read.
        artifacts_dir: Override the artifacts root directory.  When ``None``
            the value is taken from ``ARTIFACTS_ROOT`` (same as
            ``SWARM_ARTIFACTS_DIR`` env var).
    """
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
    """Read failed_step from pipeline.json for a given task_id.

    Returns empty string if not found or unreadable.

    Args:
        task_id: The task identifier whose pipeline.json to read.
        artifacts_dir: Override the artifacts root directory.  When ``None``
            the value is taken from ``ARTIFACTS_ROOT``.
    """
    root = artifacts_dir if artifacts_dir is not None else _default_artifacts_dir()
    pipeline_json: Path = root / task_id / "pipeline.json"
    if not pipeline_json.is_file():
        return ""
    try:
        data: dict[str, Any] = json.loads(pipeline_json.read_text())
        return str(data.get("failed_step") or "")
    except Exception:
        return ""

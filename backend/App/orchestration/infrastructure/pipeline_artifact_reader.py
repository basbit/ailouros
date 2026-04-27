
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.App.paths import artifacts_root

logger = logging.getLogger(__name__)

_NON_STATE_META_KEYS: frozenset[str] = frozenset({
    "partial_state",
    "failed_step",
    "resume_from_step",
    "human_approval_step",
    "error",
    "error_type",
    "human_gate_step",
    "retry_requested",
})


def _default_artifacts_dir() -> Path:
    return artifacts_root()


def reconstruct_partial_state_from_flat_snapshot(
    flat_snapshot: dict[str, Any],
) -> dict[str, Any]:
    reconstructed: dict[str, Any] = {}
    for key, value in flat_snapshot.items():
        if key in _NON_STATE_META_KEYS:
            continue
        reconstructed[key] = value

    from backend.App.orchestration.application.pipeline.step_output_extractor import (
        _AGENT_STATE_KEYS,
    )
    for agent_id, state_keys_tuple in _AGENT_STATE_KEYS.items():
        state_output_key, state_model_key, state_provider_key = state_keys_tuple
        disk_output_key = f"{agent_id}_output"
        disk_model_key = f"{agent_id}_model"
        disk_provider_key = f"{agent_id}_provider"
        if state_output_key and state_output_key != disk_output_key:
            disk_value = flat_snapshot.get(disk_output_key)
            if isinstance(disk_value, str) and disk_value and not reconstructed.get(state_output_key):
                reconstructed[state_output_key] = disk_value
        if state_model_key and state_model_key != disk_model_key:
            disk_model = flat_snapshot.get(disk_model_key)
            if isinstance(disk_model, str) and disk_model and not reconstructed.get(state_model_key):
                reconstructed[state_model_key] = disk_model
        if state_provider_key and state_provider_key != disk_provider_key:
            disk_provider = flat_snapshot.get(disk_provider_key)
            if isinstance(disk_provider, str) and disk_provider and not reconstructed.get(state_provider_key):
                reconstructed[state_provider_key] = disk_provider
    return reconstructed


def infer_failed_step_from_flat_snapshot(
    flat_snapshot: dict[str, Any],
    pipeline_steps: list[str],
    artifact_output_key_by_step: dict[str, str],
) -> str:
    for step_id in pipeline_steps:
        output_key = artifact_output_key_by_step.get(step_id)
        if not output_key:
            continue
        value = flat_snapshot.get(output_key)
        if not (isinstance(value, str) and value.strip()):
            return step_id
    return ""


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
        explicit_partial = data.get("partial_state")
        if isinstance(explicit_partial, dict) and explicit_partial:
            return explicit_partial
        return reconstruct_partial_state_from_flat_snapshot(data)
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

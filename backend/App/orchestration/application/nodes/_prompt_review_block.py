from __future__ import annotations

import logging
from typing import Any, Optional

from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState

logger = logging.getLogger(__name__)


def _read_int_env(name: str, default: int) -> int:
    import os

    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else 0
    except ValueError:
        return default
    return value if value > 0 else default


def render_embedded_pipeline_input_for_review(
    state: PipelineState,
    *,
    log_node: str,
    user_task_provider,
    compact_input_provider,
    should_use_mcp: bool,
    should_compact_for_reviewer: bool,
) -> str:
    if should_use_mcp:
        return user_task_provider(state)
    if should_compact_for_reviewer:
        pipeline_input = compact_input_provider(state)
    else:
        pipeline_input = state.get("input") or ""
    if not isinstance(pipeline_input, str):
        pipeline_input = str(pipeline_input)
    max_chars = _read_int_env("SWARM_REVIEW_PIPELINE_INPUT_MAX_CHARS", 100_000)
    task_id_prefix = (state.get("task_id") or "")[:36]
    if len(pipeline_input) <= max_chars:
        return pipeline_input
    logger.warning(
        "%s: pipeline input truncated from %d to %d chars "
        "(SWARM_REVIEW_PIPELINE_INPUT_MAX_CHARS=%d). task_id=%s",
        log_node,
        len(pipeline_input),
        max_chars,
        max_chars,
        task_id_prefix,
    )
    return (
        pipeline_input[:max_chars]
        + "\n…[pipeline input truncated — increase SWARM_REVIEW_PIPELINE_INPUT_MAX_CHARS]"
    )


def render_embedded_review_artifact(
    state: PipelineState,
    text: Any,
    *,
    log_node: str,
    part_name: str,
    env_name: str,
    default_max: int,
    mcp_max: Optional[int] = None,
    should_use_mcp: bool = False,
) -> str:
    full_text = text if isinstance(text, str) else str(text or "")
    if not full_text.strip():
        logger.warning(
            "%s: %s artifact is EMPTY — reviewer will assess a blank artifact "
            "(artifact_path_exists=False). Step output was not produced or not stored "
            "in pipeline state. Check previous pipeline steps for failures.",
            log_node,
            part_name,
        )
    if mcp_max is not None and should_use_mcp:
        effective_default = mcp_max
    else:
        effective_default = default_max
    max_chars = _read_int_env(env_name, effective_default)
    task_id_prefix = (state.get("task_id") or "")[:36]
    if len(full_text) <= max_chars:
        return full_text
    logger.warning(
        "%s: %s truncated from %d to %d chars (%s=%d). task_id=%s",
        log_node,
        part_name,
        len(full_text),
        max_chars,
        env_name,
        max_chars,
        task_id_prefix,
    )
    return full_text[:max_chars] + f"\n…[truncated — increase {env_name}]"


__all__ = (
    "render_embedded_pipeline_input_for_review",
    "render_embedded_review_artifact",
)

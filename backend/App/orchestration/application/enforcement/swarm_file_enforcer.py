from __future__ import annotations

import logging
import os
from collections.abc import Callable, Generator

from backend.App.orchestration.application.enforcement.enforcement_policy import (
    code_fence_pattern,
    swarm_file_min_lines,
    swarm_file_tag_pattern,
)
from backend.App.orchestration.application.pipeline.ephemeral_state import (
    pop_ephemeral,
    set_ephemeral,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState

_logger = logging.getLogger(__name__)


def output_has_unwrapped_code_fences(dev_output: str) -> bool:
    if os.getenv("SWARM_ENFORCE_SWARM_FILE_TAGS", "0").strip() not in ("1", "true", "yes"):
        return False
    min_lines = swarm_file_min_lines()
    swarm_tag_count = len(swarm_file_tag_pattern().findall(dev_output))
    if swarm_tag_count > 0:
        large_block_count = sum(
            1 for match in code_fence_pattern().finditer(dev_output)
            if (match.group("body") or "").count("\n") >= min_lines
        )
        if swarm_tag_count >= large_block_count:
            return False
    return any(
        (match.group("body") or "").count("\n") >= min_lines
        for match in code_fence_pattern().finditer(dev_output)
    )


def enforce_swarm_file_tags(
    state: PipelineState,
    *,
    resolve_step: Callable,
    base_agent_config: dict,
    run_step_with_stream_progress: Callable,
    emit_completed: Callable,
) -> Generator[dict, None, None]:
    dev_output = str(state.get("dev_output") or "")
    if not output_has_unwrapped_code_fences(dev_output):
        return

    _logger.warning(
        "swarm_file enforcement: dev_output contains code fences > %d lines "
        "without <swarm_file> wrappers — re-prompting Dev once.",
        swarm_file_min_lines(),
    )
    yield {
        "agent": "orchestrator",
        "status": "progress",
        "message": (
            "Dev output contains code blocks without <swarm_file path='...'> wrappers. "
            "Re-prompting Dev to wrap all file content correctly."
        ),
    }
    set_ephemeral(
        state,
        "_swarm_file_reprompt",
        "Your previous output contained code blocks that were NOT wrapped in "
        "<swarm_file path='relative/path'> tags. "
        "This is required for the workspace artifact tracker to record which files you changed. "
        "Please re-output EVERY file you intend to write, each wrapped in "
        "<swarm_file path='path/relative/to/workspace'>…content…</swarm_file>. "
        "Do not repeat unchanged files. Only include files that require changes.",
    )
    try:
        _, dev_func = resolve_step("dev", base_agent_config)
    except Exception as resolve_error:
        _logger.warning("swarm_file enforcement: could not resolve dev step: %s", resolve_error)
        return
    yield {"agent": "dev", "status": "in_progress", "message": "dev (swarm_file re-wrap)"}
    yield from run_step_with_stream_progress("dev", dev_func, state)
    yield emit_completed("dev", state)
    pop_ephemeral(state, "_swarm_file_reprompt")

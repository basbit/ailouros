"""RetryPipelineFromFailedStepUseCase — retry a pipeline from a failed step.

Strangler Fig: wraps the existing ``run_pipeline_stream_retry`` logic through
port interfaces for testability without Redis/LLM.

Rules (INV-7): no fastapi/redis/httpx/openai/anthropic/langgraph imports at module level.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.App.orchestration.domain.ports import TaskId, TaskStatus, TaskStorePort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Command / Result
# ---------------------------------------------------------------------------

@dataclass
class RetryPipelineCommand:
    """Input for RetryPipelineFromFailedStepUseCase."""

    task_id: TaskId
    failed_step: str
    partial_state: dict[str, Any] = field(default_factory=dict)
    retry_with: Optional[dict[str, Any]] = None
    agent_config: Optional[dict[str, Any]] = None


@dataclass
class RetryResult:
    """Output of RetryPipelineFromFailedStepUseCase."""

    task_id: TaskId
    status: TaskStatus
    final_text: str = ""
    last_agent: str = ""
    error: str = ""
    exc_type: str = ""
    human_approval_step: str = ""
    partial_state: dict[str, Any] = field(default_factory=dict)
    resume_from_step: str = ""


# ---------------------------------------------------------------------------
# retry_with modifiers
# ---------------------------------------------------------------------------

_VALID_RETRY_KEYS = frozenset({"different_model", "tools_off", "reduced_context"})


def _apply_retry_with(
    partial_state: dict[str, Any],
    retry_with: dict[str, Any],
    agent_config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply retry_with modifiers to partial_state and agent_config.

    Supported keys:
    - ``different_model``: str — override model for all roles.
    - ``tools_off``: bool — disable MCP tools for this retry.
    - ``reduced_context``: bool — switch workspace_context_mode to "index_only".

    Unknown keys are silently ignored (forward-compat).
    INV-1: each modifier applied is logged explicitly.

    Returns:
        Tuple of (modified partial_state, modified agent_config).
    """
    import copy

    state = copy.deepcopy(partial_state)
    ac = copy.deepcopy(agent_config)

    if not retry_with:
        return state, ac

    if "different_model" in retry_with:
        model = str(retry_with["different_model"])
        logger.info("RetryPipelineUseCase: applying retry_with.different_model=%s", model)
        # Apply to all role configs in agent_config
        for role_key in list(ac.keys()):
            if isinstance(ac[role_key], dict):
                ac[role_key]["model"] = model
            elif ac[role_key] is None:
                ac[role_key] = {"model": model}
        # Fallback: set on swarm key
        if "swarm" not in ac:
            ac["swarm"] = {}
        if isinstance(ac.get("swarm"), dict):
            ac["swarm"]["model_override"] = model

    if retry_with.get("tools_off"):
        logger.info("RetryPipelineUseCase: applying retry_with.tools_off=True")
        if "swarm" not in ac:
            ac["swarm"] = {}
        if isinstance(ac.get("swarm"), dict):
            ac["swarm"]["mcp_auto"] = False

    if retry_with.get("reduced_context"):
        logger.info("RetryPipelineUseCase: applying retry_with.reduced_context=True")
        if "swarm" not in ac:
            ac["swarm"] = {}
        if isinstance(ac.get("swarm"), dict):
            ac["swarm"]["workspace_context_mode"] = "index_only"

    return state, ac


# ---------------------------------------------------------------------------
# Use case
# ---------------------------------------------------------------------------

class RetryPipelineFromFailedStepUseCase:
    """Retry a pipeline run from a specific failed step.

    Applies ``retry_with`` modifiers (model override, tools off, reduced context)
    before re-running the pipeline via the injected ``pipeline_runner`` callable.
    """

    def __init__(
        self,
        task_store: TaskStorePort,
        pipeline_runner: Any,  # callable(partial_state, from_step, agent_config, ...) -> dict
    ) -> None:
        self._task_store = task_store
        self._pipeline_runner = pipeline_runner

    def execute(self, command: RetryPipelineCommand) -> RetryResult:
        """Retry the pipeline and return a structured result.

        Updates task_store status: in_progress → completed / failed / awaiting_human.
        """
        from backend.App.orchestration.domain.exceptions import HumanApprovalRequired

        tid = command.task_id
        logger.info(
            "RetryPipelineFromFailedStepUseCase.execute: task_id=%s failed_step=%s retry_with=%s",
            tid,
            command.failed_step,
            list((command.retry_with or {}).keys()),
        )

        self._task_store.update_task(
            tid,
            status=TaskStatus.IN_PROGRESS,
            agent="orchestrator",
            message=f"retrying from step: {command.failed_step}",
        )

        modified_state, modified_ac = _apply_retry_with(
            command.partial_state,
            command.retry_with or {},
            command.agent_config or {},
        )

        try:
            result = self._pipeline_runner(
                modified_state,
                command.failed_step,
                agent_config=modified_ac,
            )

        except HumanApprovalRequired as exc:
            self._task_store.update_task(
                tid,
                status=TaskStatus.AWAITING_HUMAN,
                agent="orchestrator",
                message=str(exc)[:2000],
            )
            return RetryResult(
                task_id=tid,
                status=TaskStatus.AWAITING_HUMAN,
                error=str(exc),
                human_approval_step=exc.step,
                partial_state=exc.partial_state or {},
                resume_from_step=exc.resume_pipeline_step or "",
            )

        except Exception as exc:
            from backend.App.orchestration.application.failure_classifier import classify_failure
            classified = classify_failure(exc)
            logger.error(
                "RetryPipelineFromFailedStepUseCase: pipeline failed: task_id=%s "
                "failure_type=%s retryable=%s mitigation=%r exc=%s",
                tid,
                classified.failure_type.value,
                classified.retryable,
                classified.suggested_mitigation,
                exc,
            )
            self._task_store.update_task(
                tid,
                status=TaskStatus.FAILED,
                agent="orchestrator",
                message=str(exc)[:2000],
            )
            return RetryResult(
                task_id=tid,
                status=TaskStatus.FAILED,
                error=str(exc),
                exc_type=type(exc).__name__,
            )

        # Success — extract final output
        final_text = ""
        last_agent = ""
        if isinstance(result, dict):
            for key in ("qa_output", "dev_output", "arch_output", "ba_output", "pm_output"):
                val = result.get(key, "")
                if val:
                    final_text = val
                    last_agent = key.replace("_output", "")
                    break
            if not final_text:
                final_text = result.get("input", "")

        self._task_store.update_task(
            tid,
            status=TaskStatus.COMPLETED,
            agent=last_agent or "orchestrator",
            message="pipeline retry completed",
        )
        logger.info(
            "RetryPipelineFromFailedStepUseCase: completed: task_id=%s agent=%s",
            tid,
            last_agent,
        )
        return RetryResult(
            task_id=tid,
            status=TaskStatus.COMPLETED,
            final_text=final_text,
            last_agent=last_agent,
        )

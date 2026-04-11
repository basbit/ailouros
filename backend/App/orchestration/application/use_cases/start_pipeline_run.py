"""StartPipelineRunUseCase — application-layer orchestration of a pipeline run.

Strangler Fig: this use case wraps the existing ``orchestrator.application.tasks``
logic through port interfaces so it can be tested without Redis/FS/MCP.

The existing ``orchestrator/api/routes_tasks.py`` still calls the legacy
``start_pipeline_run`` function directly; migrate the route to this use case
as a separate step once adapters are wired.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from backend.App.orchestration.domain.ports import TaskId, TaskStatus, TaskStorePort
from backend.App.workspace.domain.ports import WorkspaceIOPort


@runtime_checkable
class PipelineRunnerProtocol(Protocol):
    """Callable that executes a pipeline run and returns a state dict."""

    def __call__(
        self,
        user_input: str,
        agent_config: dict[str, Any],
        steps: Optional[list[str]],
        workspace_root: str,
        workspace_apply_writes: bool,
        task_id: str,
        /,
        **kwargs: Any,
    ) -> dict[str, Any]: ...


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Command / Result
# ---------------------------------------------------------------------------

@dataclass
class StartPipelineRunCommand:
    task_id: TaskId
    user_prompt: str
    effective_prompt: str
    agent_config: dict[str, Any]
    steps: Optional[list[str]]
    workspace_root_str: str
    workspace_apply_writes: bool
    workspace_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class StartPipelineRunResult:
    status: TaskStatus
    task_id: TaskId
    final_text: str = ""
    last_agent: str = ""
    error: str = ""
    exc_type: str = ""
    human_approval_step: str = ""
    partial_state: dict[str, Any] = field(default_factory=dict)
    resume_from_step: str = ""


# ---------------------------------------------------------------------------
# Use case
# ---------------------------------------------------------------------------

class StartPipelineRunUseCase:
    """Orchestrate a synchronous (non-streaming) pipeline run.

    All infrastructure access goes through ports.  The ``pipeline_runner``
    callable is injected so tests can supply a fake without a real LangGraph.
    """

    def __init__(
        self,
        task_store: TaskStorePort,
        workspace_io: WorkspaceIOPort,
        pipeline_runner: PipelineRunnerProtocol,
    ) -> None:
        self._task_store = task_store
        self._workspace_io = workspace_io
        self._pipeline_runner = pipeline_runner

    @staticmethod
    def _truthy(v: Any) -> bool:
        return bool(str(v).strip().lower() not in ("", "0", "false", "no", "off"))

    def execute(self, command: StartPipelineRunCommand) -> StartPipelineRunResult:
        """Run the pipeline and return a structured result."""
        from backend.App.orchestration.domain.exceptions import HumanApprovalRequired
        from backend.App.orchestration.application.pipeline_state import pipeline_workspace_parts_from_meta
        from backend.App.integrations.infrastructure.swarm_planner import plan_pipeline_steps as _plan

        tid = command.task_id
        logger.info(
            "StartPipelineRunUseCase.execute: task_id=%s workspace=%s",
            tid,
            bool(command.workspace_root_str.strip()),
        )

        effective_steps = list(command.steps or [])
        swarm_cfg = (command.agent_config or {}).get("swarm") or {}
        if not effective_steps and self._truthy(swarm_cfg.get("auto_plan")):
            try:
                plan_result = _plan(
                    command.user_prompt,
                    agent_config=command.agent_config,
                    constraints=str(swarm_cfg.get("auto_plan_constraints") or ""),
                )
                planned_steps = plan_result.get("pipeline_steps") or []
                if planned_steps:
                    effective_steps = planned_steps
                    logger.info(
                        "Auto-planner selected %d steps: %s (rationale: %s)",
                        len(effective_steps),
                        effective_steps,
                        str(plan_result.get("rationale") or "")[:200],
                    )
                else:
                    logger.warning("Auto-planner returned empty steps, using DEFAULT_PIPELINE_STEP_IDS")
                # Apply recommended models if planner returned them
                recommended_models = plan_result.get("recommended_models")
                if isinstance(recommended_models, dict):
                    for role, capability in recommended_models.items():
                        if role in (command.agent_config or {}) and capability == "needs_tool_calling":
                            logger.info(
                                "Auto-planner recommends tool_calling model for role '%s'", role
                            )
                        # The recommendation is logged; UI handles actual selection
            except Exception as plan_exc:
                logger.error("Auto-planner failed: %s — using default steps", plan_exc)

        try:
            self._task_store.update_task(
                tid,
                status=TaskStatus.IN_PROGRESS,
                agent="orchestrator",
                message="pipeline started",
            )

            result = self._pipeline_runner(
                command.effective_prompt,
                command.agent_config,
                effective_steps,
                command.workspace_root_str,
                command.workspace_apply_writes,
                tid.value,
                pipeline_workspace_parts=pipeline_workspace_parts_from_meta(command.workspace_meta),
                pipeline_step_ids=effective_steps,
            )

        except HumanApprovalRequired as exc:
            self._task_store.update_task(
                tid,
                status=TaskStatus.AWAITING_HUMAN,
                agent="orchestrator",
                message=str(exc)[:2000],
            )
            return StartPipelineRunResult(
                status=TaskStatus.AWAITING_HUMAN,
                task_id=tid,
                error=str(exc),
                human_approval_step=exc.step,
                partial_state=exc.partial_state or {},
                resume_from_step=exc.resume_pipeline_step or "",
            )
        except Exception as exc:
            self._task_store.update_task(
                tid,
                status=TaskStatus.FAILED,
                agent="orchestrator",
                message=str(exc)[:2000],
            )
            return StartPipelineRunResult(
                status=TaskStatus.FAILED,
                task_id=tid,
                error=str(exc),
                exc_type=type(exc).__name__,
            )

        # Success — extract final output from result state
        final_text = ""
        last_agent = ""
        if isinstance(result, dict):
            # Try common output keys in order of preference
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
            message="pipeline completed",
        )
        return StartPipelineRunResult(
            status=TaskStatus.COMPLETED,
            task_id=tid,
            final_text=final_text,
            last_agent=last_agent,
        )

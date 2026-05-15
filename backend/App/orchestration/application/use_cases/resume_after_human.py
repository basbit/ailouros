
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.App.orchestration.domain.ports import TaskId, TaskStatus, TaskStorePort

logger = logging.getLogger(__name__)


@dataclass
class ResumeAfterHumanCommand:

    task_id: TaskId
    feedback: str
    partial_state: dict[str, Any] = field(default_factory=dict)
    resume_from_step: str = ""
    agent_config: Optional[dict[str, Any]] = None


@dataclass
class ResumeResult:

    task_id: TaskId
    status: TaskStatus
    final_text: str = ""
    last_agent: str = ""
    error: str = ""
    exc_type: str = ""
    human_approval_step: str = ""
    partial_state: dict[str, Any] = field(default_factory=dict)
    resume_from_step: str = ""


class ResumeAfterHumanApprovalUseCase:

    def __init__(
        self,
        task_store: TaskStorePort,
        pipeline_runner: Any,
    ) -> None:
        self._task_store = task_store
        self._pipeline_runner = pipeline_runner

    def execute(self, command: ResumeAfterHumanCommand) -> ResumeResult:
        from backend.App.orchestration.domain.exceptions import HumanApprovalRequired

        tid = command.task_id
        logger.info(
            "ResumeAfterHumanApprovalUseCase.execute: task_id=%s step=%s",
            tid,
            command.resume_from_step,
        )

        self._task_store.update_task(
            tid,
            status=TaskStatus.IN_PROGRESS,
            agent="orchestrator",
            message="resuming after human approval",
        )

        try:
            result = self._pipeline_runner(
                command.partial_state,
                command.resume_from_step,
                command.feedback,
                agent_config=command.agent_config or {},
            )

        except HumanApprovalRequired as exc:
            self._task_store.update_task(
                tid,
                status=TaskStatus.AWAITING_HUMAN,
                agent="orchestrator",
                message=str(exc)[:2000],
            )
            return ResumeResult(
                task_id=tid,
                status=TaskStatus.AWAITING_HUMAN,
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
            logger.error(
                "ResumeAfterHumanApprovalUseCase: pipeline failed: task_id=%s exc=%s",
                tid,
                exc,
            )
            return ResumeResult(
                task_id=tid,
                status=TaskStatus.FAILED,
                error=str(exc),
                exc_type=type(exc).__name__,
            )

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
            message="pipeline resumed and completed",
        )
        logger.info(
            "ResumeAfterHumanApprovalUseCase: completed: task_id=%s agent=%s",
            tid,
            last_agent,
        )
        return ResumeResult(
            task_id=tid,
            status=TaskStatus.COMPLETED,
            final_text=final_text,
            last_agent=last_agent,
        )

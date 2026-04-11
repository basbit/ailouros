"""Orchestration domain exceptions."""

from __future__ import annotations

from typing import Any, Optional


class PipelineCancelled(Exception):
    """Pipeline stopped by client disconnect or server shutdown."""

    pass


class HumanGateTimeout(Exception):
    """Human gate waited longer than SWARM_HUMAN_GATE_TIMEOUT_SEC — pipeline aborted."""

    def __init__(self, step: str, timeout_sec: float) -> None:
        super().__init__(
            f"[human:{step}] Timeout after {timeout_sec:.0f}s — no operator response. "
            "Pipeline aborted. Resume via POST /v1/tasks/{id}/human-resume."
        )
        self.step = step
        self.timeout_sec = timeout_sec


class HumanApprovalRequired(Exception):
    """Stop: `agent_config.human.require_manual` and no auto_approve."""

    def __init__(
        self,
        step: str,
        detail: str,
        *,
        partial_state: Optional[dict[str, Any]] = None,
        resume_pipeline_step: Optional[str] = None,
    ) -> None:
        super().__init__(detail)
        self.step = step
        self.detail = detail
        self.partial_state = partial_state or {}
        self.resume_pipeline_step = resume_pipeline_step

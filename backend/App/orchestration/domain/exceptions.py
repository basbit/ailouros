
from __future__ import annotations

from typing import Any, Optional

from backend.App.shared.domain.exceptions import DomainError, OperationCancelled


class PipelineCancelled(OperationCancelled, DomainError):
    def __init__(self, detail: str = "pipeline cancelled") -> None:
        OperationCancelled.__init__(self, source="pipeline", detail=detail)


class HumanGateTimeout(DomainError):

    def __init__(self, step: str, timeout_sec: float) -> None:
        super().__init__(
            f"[human:{step}] Timeout after {timeout_sec:.0f}s — no operator response. "
            "Pipeline aborted. Resume via POST /v1/tasks/{id}/human-resume."
        )
        self.step = step
        self.timeout_sec = timeout_sec


class HumanApprovalRequired(DomainError):

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


class LlmProviderUnconfigured(DomainError):

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from backend.App.orchestration.domain.delegation import (
    DelegationBranch,
    DelegationRequest,
    DelegationResult,
    DelegationStatus,
)
from backend.App.orchestration.domain.ports import AgentDelegationPort

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class LocalDelegationAdapter(AgentDelegationPort):

    def __init__(
        self,
        agent_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._agent_factory = agent_factory
        self._branches: dict[str, DelegationBranch] = {}
        self._results: dict[str, DelegationResult] = {}
        self._lock = threading.Lock()

    def delegate(self, request: DelegationRequest) -> DelegationBranch:
        branch_id = str(uuid.uuid4())
        branch = DelegationBranch(
            branch_id=branch_id,
            delegation_id=request.delegation_id,
            session_id=request.parent_session_id,
            status=DelegationStatus.PENDING,
        )
        with self._lock:
            self._branches[branch_id] = branch

        thread = threading.Thread(
            target=self._run_branch,
            args=(branch_id, request),
            daemon=True,
        )
        thread.start()
        logger.info("Delegation branch %s spawned for role '%s'", branch_id, request.role)
        return branch

    def join(self, branch_id: str, *, timeout_sec: int = 300) -> DelegationResult:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            with self._lock:
                result = self._results.get(branch_id)
            if result is not None:
                return result
            time.sleep(0.5)
        return DelegationResult(
            delegation_id=branch_id,
            status=DelegationStatus.FAILED,
            output="",
            error=f"Delegation join timed out after {timeout_sec}s",
            completed_at=_now_iso(),
        )

    def cancel(self, branch_id: str) -> None:
        with self._lock:
            branch = self._branches.get(branch_id)
            if branch:
                branch.status = DelegationStatus.CANCELLED
        logger.info("Delegation branch %s cancelled", branch_id)

    def _run_branch(self, branch_id: str, request: DelegationRequest) -> None:
        started = time.monotonic()
        with self._lock:
            if branch := self._branches.get(branch_id):
                branch.status = DelegationStatus.RUNNING

        try:
            output = self._execute_role(request)
            result = DelegationResult(
                delegation_id=request.delegation_id,
                status=DelegationStatus.COMPLETED,
                output=output,
                elapsed_sec=round(time.monotonic() - started, 3),
                completed_at=_now_iso(),
            )
        except Exception as exc:
            logger.exception("Delegation branch %s failed: %s", branch_id, exc)
            result = DelegationResult(
                delegation_id=request.delegation_id,
                status=DelegationStatus.FAILED,
                output="",
                error=str(exc),
                elapsed_sec=round(time.monotonic() - started, 3),
                completed_at=_now_iso(),
            )

        with self._lock:
            self._results[branch_id] = result
            if branch := self._branches.get(branch_id):
                branch.status = result.status
                branch.result = result

    def _execute_role(self, request: DelegationRequest) -> str:
        if self._agent_factory is None:
            return f"[LocalDelegation] role={request.role} task={request.task_description[:80]}"
        agent = self._agent_factory(request.role)
        return agent.run(request.task_description)

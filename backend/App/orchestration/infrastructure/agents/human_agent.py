
from __future__ import annotations

import logging
import os
import time
import threading
from typing import Any, Optional

from backend.App.orchestration.domain.exceptions import HumanApprovalRequired, HumanGateTimeout

logger = logging.getLogger(__name__)


def _human_gate_timeout_sec() -> float:
    try:
        v = float(os.getenv("SWARM_HUMAN_GATE_TIMEOUT_SEC", "3600"))
        return max(1.0, v)
    except ValueError:
        return 3600.0


class HumanAgent:

    def __init__(
        self,
        step: str,
        agent_config: Optional[dict[str, Any]] = None,
        *,
        task_id: Optional[str] = None,
        task_store: Any = None,
    ) -> None:
        self.step = step
        self.agent_config = agent_config or {}
        self.task_id = task_id
        self.task_store = task_store
        self.used_model = ""
        self.used_provider = "human"

    def run(
        self,
        context: str,
        *,
        wait_event: Optional[threading.Event] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> str:
        auto = self.agent_config.get("auto_approve")
        if auto is None:
            auto = os.getenv("SWARM_HUMAN_AUTO_APPROVE", "1") == "1"
        else:
            auto = bool(auto)

        if self.agent_config.get("require_manual"):
            auto = False

        _preview_chars = int(os.environ.get("SWARM_HUMAN_PREVIEW_CHARS", "800"))
        preview = (context or "").strip()
        if len(preview) > _preview_chars:
            preview = preview[:_preview_chars] + "\n…"

        if auto:
            return (
                f"[human:{self.step}] APPROVED (auto). "
                f"См. артефакты и ревью выше. Контекст ~{len(context)} символов "
                f"(размер bundle для human). Ручной ввод: SWARM_HUMAN_AUTO_APPROVE=0 "
                f"или agent_config.human.require_manual / чекбокс в /ui."
            )

        if wait_event is not None:
            timeout_sec = _human_gate_timeout_sec()
            deadline = time.monotonic() + timeout_sec
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise HumanApprovalRequired(
                        self.step,
                        f"[human:{self.step}] Pipeline cancelled while waiting for human approval.",
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise HumanGateTimeout(self.step, timeout_sec)
                if wait_event.wait(timeout=min(1.0, remaining)):
                    return f"[human:{self.step}] APPROVED (operator confirmed via wait_event)."

        if self.task_id and self.task_store:
            from backend.App.orchestration.infrastructure.human_approval import (
                request_human_approval,
            )
            logger.info("human gate: blocking for approval task=%s step=%s", self.task_id, self.step)
            approved, user_input = request_human_approval(
                self.task_id, self.step, context, self.task_store,
                cancel_event=cancel_event,
            )
            if approved:
                suffix = f" Ответ: {user_input}" if user_input else ""
                return f"[human:{self.step}] Confirmed manually.{suffix}"
            return f"[human:{self.step}] REJECTED by user."

        detail = (
            f"[human:{self.step}] Manual approval required. "
            f"Review the output, then set "
            f"SWARM_HUMAN_AUTO_APPROVE=1 or agent_config.human.auto_approve=true to auto-approve. "
            f"Preview:\n{preview}"
        )
        raise HumanApprovalRequired(self.step, detail)

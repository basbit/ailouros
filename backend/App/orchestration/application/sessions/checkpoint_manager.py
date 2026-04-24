
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

_EVERY_N_DEFAULT = 1
_TTL_HOURS_DEFAULT = 24


@dataclass
class Checkpoint:

    task_id: str
    step_id: str
    step_index: int
    state_snapshot: dict[str, Any]
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "task_id": self.task_id,
            "step_id": self.step_id,
            "step_index": self.step_index,
            "state_snapshot": self.state_snapshot,
            "timestamp": self.timestamp,
            "timestamp_utc": _iso_from_epoch(self.timestamp),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Checkpoint":
        return cls(
            task_id=data["task_id"],
            step_id=data["step_id"],
            step_index=data.get("step_index", 0),
            state_snapshot=data.get("state_snapshot", {}),
            timestamp=data.get("timestamp", 0.0),
        )


def _iso_from_epoch(ts: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(
        ts, tz=datetime.timezone.utc,
    ).isoformat()


class CheckpointManager:

    def __init__(
        self,
        store: Any = None,
        every_n_steps: Optional[int] = None,
        ttl_hours: Optional[int] = None,
    ) -> None:
        self._store = store
        self._every_n = every_n_steps if every_n_steps is not None else int(
            os.getenv("SWARM_CHECKPOINT_EVERY_N_STEPS", str(_EVERY_N_DEFAULT))
        )
        self._ttl_hours = ttl_hours if ttl_hours is not None else int(
            os.getenv("SWARM_CHECKPOINT_TTL_HOURS", str(_TTL_HOURS_DEFAULT))
        )
        self._memory: dict[str, list[Checkpoint]] = {}

    def save(self, task_id: str, step_id: str, step_index: int, state: dict[str, Any]) -> None:
        if self._every_n > 1 and step_index > 0 and (step_index + 1) % self._every_n != 0:
            return

        cp = Checkpoint(
            task_id=task_id,
            step_id=step_id,
            step_index=step_index,
            state_snapshot=dict(state),
            timestamp=time.time(),
        )
        self._memory.setdefault(task_id, []).append(cp)
        logger.info(
            "checkpoint: saved task=%s step=%s index=%d",
            task_id, step_id, step_index,
        )

    def get_latest(self, task_id: str) -> Optional[Checkpoint]:
        cps = self._memory.get(task_id, [])
        return cps[-1] if cps else None

    def get_by_step(self, task_id: str, step_id: str) -> Optional[Checkpoint]:
        for cp in reversed(self._memory.get(task_id, [])):
            if cp.step_id == step_id:
                return cp
        return None

    def list_checkpoints(self, task_id: str) -> list[dict[str, Any]]:
        return [cp.to_dict() for cp in self._memory.get(task_id, [])]

    def resume_state(self, task_id: str, step_id: str) -> Optional[dict[str, Any]]:
        cp = self.get_by_step(task_id, step_id)
        if cp is None:
            logger.info("checkpoint: no checkpoint found task=%s step=%s", task_id, step_id)
            return None
        logger.info(
            "checkpoint: resuming task=%s from step=%s index=%d",
            task_id, step_id, cp.step_index,
        )
        return dict(cp.state_snapshot)

    def clear(self, task_id: str) -> None:
        removed = len(self._memory.pop(task_id, []))
        if removed:
            logger.info("checkpoint: cleared %d checkpoints for task=%s", removed, task_id)

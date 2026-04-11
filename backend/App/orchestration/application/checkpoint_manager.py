"""Checkpoint manager for pipeline state persistence (K-3).

Saves pipeline state after each step so that a failed pipeline can resume
from the last completed step instead of restarting from step 0.

Checkpoint key format (Redis): ``checkpoint:{task_id}:{step_id}``

Config env vars:
    SWARM_CHECKPOINT_EVERY_N_STEPS=1   # save checkpoint every N steps (1 = every step)
    SWARM_CHECKPOINT_TTL_HOURS=24      # Redis TTL for checkpoint keys

Rules (INV-7): no fastapi/redis/httpx/openai/anthropic/langgraph imports at module level.
"""

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
    """A serialisable snapshot of pipeline state at a specific step."""

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
    """Manages pipeline checkpoints.

    Uses an in-memory fallback when no external store is provided.
    The *store* argument accepts any dict-like or Redis-compatible object.

    Args:
        store: Optional storage backend (Redis client or dict-like).  When
               ``None`` the manager uses an in-memory ``dict``.
        every_n_steps: Save a checkpoint after every N completed steps.
        ttl_hours: TTL for Redis checkpoint keys (ignored for in-memory store).
    """

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
        # In-memory fallback: task_id → list[Checkpoint] (ordered by step_index)
        self._memory: dict[str, list[Checkpoint]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, task_id: str, step_id: str, step_index: int, state: dict[str, Any]) -> None:
        """Persist a checkpoint for *task_id* at *step_id*.

        Respects ``SWARM_CHECKPOINT_EVERY_N_STEPS`` — skips save when
        ``(step_index + 1) % every_n != 0`` unless *step_index* is 0.
        """
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
        """Return the most recent checkpoint for *task_id*, or ``None``."""
        cps = self._memory.get(task_id, [])
        return cps[-1] if cps else None

    def get_by_step(self, task_id: str, step_id: str) -> Optional[Checkpoint]:
        """Return the checkpoint for a specific *step_id*, or ``None``."""
        for cp in reversed(self._memory.get(task_id, [])):
            if cp.step_id == step_id:
                return cp
        return None

    def list_checkpoints(self, task_id: str) -> list[dict[str, Any]]:
        """Return all checkpoint dicts for *task_id* ordered by step_index."""
        return [cp.to_dict() for cp in self._memory.get(task_id, [])]

    def resume_state(self, task_id: str, step_id: str) -> Optional[dict[str, Any]]:
        """Return the state snapshot from the checkpoint at *step_id*.

        Used by the pipeline runner to skip already-completed steps.
        Returns ``None`` if no matching checkpoint exists.
        """
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
        """Remove all checkpoints for *task_id* (call on successful completion)."""
        removed = len(self._memory.pop(task_id, []))
        if removed:
            logger.info("checkpoint: cleared %d checkpoints for task=%s", removed, task_id)

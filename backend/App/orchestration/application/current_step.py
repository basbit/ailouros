"""Task-local current-step anchor.

Thread and asyncio safe via :class:`~contextvars.ContextVar`. The pipeline
step decorator pins the active ``step_id`` (and the owning ``agent_config``)
here for the duration of the step. Infrastructure below the agent layer —
most notably the LLM client — reads these values to apply per-step tuning
(e.g. role-aware reasoning budget) *without* having to thread ``step_id``
through every layer of API.

Usage::

    with current_step("dev", agent_config=state.get("agent_config")):
        run_agent_with_boundary(state, agent, prompt)  # ask_model picks up step_id
        helper.do_something()                          # same

Outside the block the values fall back to ``None`` so non-pipeline callers
(CLI scripts, tests) see no implicit per-step behaviour.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

_current_step_id: ContextVar[Optional[str]] = ContextVar("current_step_id", default=None)
_current_agent_config: ContextVar[Optional[dict[str, Any]]] = ContextVar(
    "current_agent_config", default=None
)


def get_current_step_id() -> Optional[str]:
    """Return the step_id pinned by the active step decorator, or ``None``."""
    return _current_step_id.get()


def get_current_agent_config() -> Optional[dict[str, Any]]:
    """Return the agent_config captured at step entry, or ``None``."""
    return _current_agent_config.get()


@contextmanager
def current_step(
    step_id: str,
    *,
    agent_config: Optional[dict[str, Any]] = None,
) -> Iterator[None]:
    """Pin *step_id* (+ optional agent_config) while the ``with`` block runs.

    ContextVar.set returns a token whose .reset() is called in the finally
    block, so nested ``current_step`` calls restore the outer value cleanly
    (useful when enforcement flows re-run a step while another is in flight).
    """
    tok_step = _current_step_id.set(step_id)
    tok_cfg = _current_agent_config.set(agent_config)
    try:
        yield
    finally:
        _current_agent_config.reset(tok_cfg)
        _current_step_id.reset(tok_step)


__all__ = [
    "current_step",
    "get_current_step_id",
    "get_current_agent_config",
]

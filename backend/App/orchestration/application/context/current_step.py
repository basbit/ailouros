
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

_current_step_id: ContextVar[Optional[str]] = ContextVar("current_step_id", default=None)
_current_agent_config: ContextVar[Optional[dict[str, Any]]] = ContextVar(
    "current_agent_config", default=None
)


def get_current_step_id() -> Optional[str]:
    return _current_step_id.get()


def get_current_agent_config() -> Optional[dict[str, Any]]:
    return _current_agent_config.get()


@contextmanager
def current_step(
    step_id: str,
    *,
    agent_config: Optional[dict[str, Any]] = None,
) -> Iterator[None]:
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

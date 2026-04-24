"""Typed helpers for reading/writing **ephemeral** keys on ``PipelineState``.

``PipelineState`` is a TypedDict with a frozen set of declared keys (see
ADR-pipeline-state in ``CLAUDE.md``). Short-lived runtime signals that are
**not** part of that contract — ``_swarm_file_reprompt``, ``_post_write_issues``,
``deep_planning_error``, ``_needs_work_count``, ``workspace_writes``,
``workspace_evidence_brief``, ``mcp_tool_call_suspected_failure`` and friends —
are accessed via ``cast(dict[str, Any], state)[...]`` to bypass the TypedDict
invariance. That pattern was sprinkled across 10+ files.

This module exposes typed wrappers so the intent is explicit ("this is an
ephemeral signal, not a declared state field"). It's kept inside
``orchestration/application/pipeline`` rather than ``shared`` because it is
tightly coupled to ``PipelineState`` semantics — no other domain uses it.

Usage::

    from backend.App.orchestration.application.pipeline.ephemeral_state import (
        set_ephemeral, get_ephemeral, pop_ephemeral, append_ephemeral,
    )

    set_ephemeral(state, "_swarm_file_reprompt", "...")
    val = get_ephemeral(state, "deep_planning_error", default="")
    append_ephemeral(state, "_post_write_issues", {"file": "...", ...})
    pop_ephemeral(state, "_swarm_file_reprompt")
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional, TypeVar, cast

__all__ = [
    "append_ephemeral",
    "ephemeral_as_dict",
    "get_ephemeral",
    "pop_ephemeral",
    "set_ephemeral",
    "update_ephemeral",
]


T = TypeVar("T")


def set_ephemeral(state: Any, key: str, value: Any) -> None:
    """Set an ephemeral key on ``state`` (bypasses TypedDict typing)."""
    cast(dict[str, Any], state)[key] = value


def get_ephemeral(state: Any, key: str, default: Optional[T] = None) -> Optional[T] | Any:
    """Read an ephemeral key from ``state`` returning ``default`` if missing."""
    if isinstance(state, Mapping):
        return state.get(key, default)  # type: ignore[return-value]
    return cast(dict[str, Any], state).get(key, default)


def pop_ephemeral(state: Any, key: str, default: Optional[T] = None) -> Optional[T] | Any:
    """Remove and return an ephemeral key (no-op if missing)."""
    return cast(dict[str, Any], state).pop(key, default)


def append_ephemeral(state: Any, key: str, item: Any) -> None:
    """Append ``item`` to ``state[key]`` (creating the list if absent)."""
    cast(dict[str, Any], state).setdefault(key, []).append(item)


def update_ephemeral(state: Any, delta: Mapping[str, Any]) -> None:
    """Bulk-update ephemeral keys from a mapping."""
    cast(dict[str, Any], state).update(delta)


def ephemeral_as_dict(state: Any) -> dict[str, Any]:
    """Return ``state`` re-typed as ``dict[str, Any]`` for ad-hoc mutation.

    Prefer the typed helpers above; this escape hatch exists for places
    that need bulk/iterator access to multiple ephemeral keys at once.
    """
    return cast(dict[str, Any], state)

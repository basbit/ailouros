"""ChatRequestResolver — parses agent_config and pipeline_steps from request data.

No FastAPI imports, no filesystem operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from backend.App.integrations.infrastructure.agent_registry import merge_agent_config
from backend.App.integrations.infrastructure.pipeline_presets import resolve_preset


@dataclass
class ChatRequest:
    """Parsed chat request with resolved agent config and pipeline steps."""

    agent_config: dict[str, Any]
    pipeline_steps: Optional[list[str]]


class ChatRequestResolver:
    """Parse ``agent_config`` and ``pipeline_steps`` from raw request data.

    This is a pure class — it performs no I/O and has no FastAPI dependencies.
    """

    def resolve(self, request_data: Any) -> ChatRequest:
        """Resolve and return a :class:`ChatRequest` from *request_data*.

        *request_data* must expose ``.agent_config``, ``.pipeline_steps``, and
        ``.pipeline_preset`` attributes (e.g. a Pydantic model or a similar
        object).

        Returns:
            A :class:`ChatRequest` with merged ``agent_config`` and resolved
            ``pipeline_steps``.
        """
        agent_config = merge_agent_config(request_data.agent_config)
        steps: Optional[list[str]] = request_data.pipeline_steps
        if steps is None and request_data.pipeline_preset:
            steps = resolve_preset(request_data.pipeline_preset)
        return ChatRequest(agent_config=agent_config, pipeline_steps=steps)

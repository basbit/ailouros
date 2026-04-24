
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from backend.App.integrations.infrastructure.agent_registry import merge_agent_config
from backend.App.integrations.infrastructure.pipeline_presets import resolve_preset


@dataclass
class ChatRequest:

    agent_config: dict[str, Any]
    pipeline_steps: Optional[list[str]]


class ChatRequestResolver:

    def resolve(self, request_data: Any) -> ChatRequest:
        agent_config = merge_agent_config(request_data.agent_config)
        steps: Optional[list[str]] = request_data.pipeline_steps
        if steps is None and request_data.pipeline_preset:
            steps = resolve_preset(request_data.pipeline_preset)
        return ChatRequest(agent_config=agent_config, pipeline_steps=steps)

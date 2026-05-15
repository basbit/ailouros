
from __future__ import annotations

import copy
from typing import Any

__all__ = ["apply_retry_with_to_agent_config"]


def apply_retry_with_to_agent_config(
    agent_config: dict[str, Any],
    partial_state: dict[str, Any],
    retry_with: Any,
) -> dict[str, Any]:
    updated_config = copy.deepcopy(agent_config)
    if retry_with.different_model:
        model = retry_with.different_model.strip()
        for role_cfg in updated_config.values():
            if isinstance(role_cfg, dict):
                role_cfg["model"] = model
    if retry_with.tools_off is True:
        for role_cfg in updated_config.values():
            if isinstance(role_cfg, dict):
                mcp = role_cfg.get("mcp")
                if isinstance(mcp, dict):
                    mcp["servers"] = []
    if retry_with.reduced_context:
        partial_state["workspace_context_mode"] = retry_with.reduced_context
    return updated_config

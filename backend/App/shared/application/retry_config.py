"""Apply ``retry_with`` modifiers to an existing agent_config.

Before this module lived, two *identical* copies of the logic existed:

  * ``shared/infrastructure/rest/utils.py::_apply_retry_with``
  * ``orchestration/application/streaming/retry_stream.py::apply_retry_with_to_agent_config``

Tests reference both names, so this module exports the canonical
implementation and both historical names as aliases.

There is a *different* ``_apply_retry_with`` in
``orchestration/application/use_cases/retry_pipeline.py`` — it accepts a
plain ``dict`` for ``retry_with`` and writes to ``swarm.*`` subkeys rather
than role-level ``model`` / ``mcp.servers``. That function is semantically
different and stays where it is.
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = ["apply_retry_with_to_agent_config"]


def apply_retry_with_to_agent_config(
    agent_config: dict[str, Any],
    partial_state: dict[str, Any],
    retry_with: Any,
) -> dict[str, Any]:
    """Return a deep-copied ``agent_config`` with ``retry_with`` modifiers applied.

    ``retry_with`` is expected to be an object with attributes
    ``different_model`` (str), ``tools_off`` (bool), ``reduced_context``
    (str). ``partial_state`` is mutated in place when
    ``retry_with.reduced_context`` is set, so the caller sees the updated
    ``workspace_context_mode``.
    """
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

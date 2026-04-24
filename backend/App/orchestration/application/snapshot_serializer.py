"""Orchestration-specific pipeline snapshot serialisation.

The cross-cutting secret-redaction helper moved to
``shared.application.secrets_redaction`` — re-exported here so existing
imports keep working.
"""

from __future__ import annotations

import copy
from typing import Any

from backend.App.shared.application.secrets_redaction import (
    redact_agent_config_secrets,
)

__all__ = [
    "pipeline_snapshot_for_disk",
    "redact_agent_config_secrets",
]


def pipeline_snapshot_for_disk(snap: dict[str, Any]) -> dict[str, Any]:
    """Prepare a pipeline snapshot for on-disk / trace persistence.

    Redacts API keys in both the top-level ``agent_config`` and any nested
    ``partial_state.agent_config``.
    """
    out = copy.deepcopy(snap)
    ac = out.get("agent_config")
    if isinstance(ac, dict):
        out["agent_config"] = redact_agent_config_secrets(ac)
    ps = out.get("partial_state")
    if isinstance(ps, dict):
        ps_ac = ps.get("agent_config")
        if isinstance(ps_ac, dict):
            ps["agent_config"] = redact_agent_config_secrets(ps_ac)
    return out

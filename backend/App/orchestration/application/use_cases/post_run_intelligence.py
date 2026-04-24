from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.App.orchestration.domain.agent_identity import build_agent_profiles
from backend.App.orchestration.domain.automation_agents import build_automation_agent_report


def persist_post_run_intelligence(task_dir: Path, workspace_root: Path | None, pipeline_snapshot: dict[str, Any]) -> dict[str, Any]:
    automation_report = build_automation_agent_report(pipeline_snapshot)
    identity_report = {
        "schema": "swarm_agent_identity/v1",
        "profiles": build_agent_profiles(pipeline_snapshot),
    }
    (task_dir / "automation_agents.json").write_text(
        json.dumps(automation_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (task_dir / "agent_identity.json").write_text(
        json.dumps(identity_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if workspace_root is not None:
        swarm_dir = workspace_root / ".swarm"
        swarm_dir.mkdir(parents=True, exist_ok=True)
        (swarm_dir / "agent_identity.json").write_text(
            json.dumps(identity_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return {
        "automation_agents": automation_report,
        "agent_identity": identity_report,
    }

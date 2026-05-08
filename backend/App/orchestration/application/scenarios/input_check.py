from __future__ import annotations

from typing import Any

from backend.App.orchestration.domain.scenarios.scenario import Scenario


def collect_missing_required_inputs(
    scenario: Scenario,
    user_prompt: str,
    workspace_root: Any,
    project_context_file: Any,
) -> list[str]:
    missing: list[str] = []
    for spec in scenario.inputs:
        if not spec.required:
            continue
        if spec.key == "prompt":
            if not isinstance(user_prompt, str) or not user_prompt.strip():
                missing.append(spec.key)
        elif spec.key == "workspace_root":
            if not isinstance(workspace_root, str) or not workspace_root.strip():
                missing.append(spec.key)
        elif spec.key == "project_context_file":
            if not isinstance(project_context_file, str) or not project_context_file.strip():
                missing.append(spec.key)
    return missing

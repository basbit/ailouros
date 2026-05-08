from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState


def is_path_covered(expected: str, produced_paths: list[str]) -> bool:
    expected_norm = expected.lstrip("./").replace("\\", "/")
    expected_basename = expected_norm.rsplit("/", 1)[-1]
    for produced in produced_paths:
        produced_norm = produced.lstrip("./").replace("\\", "/")
        if produced_norm == expected_norm:
            return True
        if (
            produced_norm.endswith("/" + expected_norm)
            or expected_norm.endswith("/" + produced_norm)
        ):
            return True
        produced_basename = produced_norm.rsplit("/", 1)[-1]
        if expected_basename and produced_basename == expected_basename:
            return True
    return False


def normalize_produced_path(raw: str, workspace_root: str) -> str:
    if not raw:
        return raw
    normalized = raw.replace("\\", "/")
    if workspace_root:
        prefix = workspace_root.rstrip("/").replace("\\", "/") + "/"
        if normalized.startswith(prefix):
            return normalized[len(prefix):]
        if normalized == prefix.rstrip("/"):
            return "."
    return normalized


def extract_subtask_workspace_contract(
    state: PipelineState,
    output: str,
    *,
    mcp_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    from backend.App.workspace.infrastructure.patch_parser import apply_workspace_pipeline

    produced_paths: list[str] = []
    workspace_root = str(state.get("workspace_root") or "").strip()
    if workspace_root:
        dry_run = apply_workspace_pipeline(
            output or "",
            Path(workspace_root),
            dry_run=True,
            run_shell=False,
        )
        for key in ("written", "patched", "udiff_applied"):
            for rel in dry_run.get(key, []) or []:
                norm = normalize_produced_path(rel, workspace_root)
                if norm and norm not in produced_paths:
                    produced_paths.append(norm)
    for action in mcp_actions:
        if not isinstance(action, dict):
            continue
        raw = str(action.get("path") or "").strip()
        norm = normalize_produced_path(raw, workspace_root)
        if norm and norm not in produced_paths:
            produced_paths.append(norm)
    return {"produced_paths": produced_paths}


__all__ = (
    "is_path_covered",
    "normalize_produced_path",
    "extract_subtask_workspace_contract",
)

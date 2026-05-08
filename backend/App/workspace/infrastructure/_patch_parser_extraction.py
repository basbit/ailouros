from __future__ import annotations

import re
from typing import Any

_PAT_BASH_FENCE = re.compile(r"```(?:bash|sh)\n(.*?)```", re.DOTALL)


_SWARM_ACTION_MARKERS: tuple[str, ...] = (
    "<swarm_file",
    "<swarm_patch",
    "<swarm-shell",
    "<swarm_shell",
    "<swarm-command",
)


def extract_commands_from_bare_bash_fences(text: str) -> list[str]:
    commands: list[str] = []
    for match in _PAT_BASH_FENCE.finditer(text):
        for line in match.group(1).splitlines():
            line_text = line.strip()
            if (
                not line_text
                or line_text.startswith("#")
                or "<swarm_" in line_text.lower()
            ):
                continue
            commands.append(line_text)
    return commands


def text_contains_swarm_workspace_actions(text: str) -> bool:
    return any(marker in text for marker in _SWARM_ACTION_MARKERS)


def any_snapshot_output_has_swarm(state: dict[str, Any]) -> bool:
    for key, value in state.items():
        if isinstance(key, str) and key.endswith("_output") and isinstance(value, str):
            if text_contains_swarm_workspace_actions(value):
                return True
    for list_key in ("dev_task_outputs", "qa_task_outputs"):
        arr = state.get(list_key)
        if isinstance(arr, list):
            for piece in arr:
                if (
                    isinstance(piece, str)
                    and text_contains_swarm_workspace_actions(piece)
                ):
                    return True
    return False


def collect_workspace_source_chunks(
    state: dict[str, Any],
    *,
    workspace_swarm_file_source_keys: tuple[str, ...],
) -> list[str]:
    chunks: list[str] = []
    seen_keys: set[str] = set()

    steps = state.get("pipeline_steps")
    if isinstance(steps, list):
        for raw_step in steps:
            step_id = str(raw_step).strip()
            if not step_id:
                continue
            key = f"{step_id}_output"
            text = state.get(key)
            if isinstance(text, str) and text.strip():
                chunks.append(text)
                seen_keys.add(key)

    if not chunks:
        for key in workspace_swarm_file_source_keys:
            text = state.get(key)
            if isinstance(text, str) and text.strip():
                chunks.append(text)
                seen_keys.add(key)

    if "dev_output" not in seen_keys:
        arr = state.get("dev_task_outputs")
        if isinstance(arr, list):
            for piece in arr:
                if isinstance(piece, str) and piece.strip():
                    chunks.append(piece)

    if "qa_output" not in seen_keys:
        arr = state.get("qa_task_outputs")
        if isinstance(arr, list):
            for piece in arr:
                if isinstance(piece, str) and piece.strip():
                    chunks.append(piece)

    for key in sorted(state.keys()):
        if not isinstance(key, str) or not key.endswith("_output"):
            continue
        if key in seen_keys:
            continue
        text = state.get(key)
        if not isinstance(text, str) or not text.strip():
            continue
        if not text_contains_swarm_workspace_actions(text):
            continue
        chunks.append(text)
        seen_keys.add(key)

    return chunks


def merged_workspace_source_text(
    state: dict[str, Any],
    *,
    workspace_swarm_file_source_keys: tuple[str, ...],
) -> str:
    chunks = collect_workspace_source_chunks(
        state,
        workspace_swarm_file_source_keys=workspace_swarm_file_source_keys,
    )
    if not chunks:
        return ""
    return "\n\n".join(chunks)


__all__ = (
    "extract_commands_from_bare_bash_fences",
    "text_contains_swarm_workspace_actions",
    "any_snapshot_output_has_swarm",
    "collect_workspace_source_chunks",
    "merged_workspace_source_text",
)

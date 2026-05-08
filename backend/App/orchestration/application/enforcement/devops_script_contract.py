from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from backend.App.orchestration.application.pipeline.ephemeral_state import (
    ephemeral_as_dict,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.shared.infrastructure.app_config_load import load_app_config_json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScriptFinding:
    path: str
    reason: str
    verbs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["verbs"] = list(self.verbs)
        return data


@lru_cache(maxsize=1)
def _policy() -> dict[str, Any]:
    return load_app_config_json("devops_script_contract.json")


def _string_tuple(key: str) -> tuple[str, ...]:
    raw = _policy().get(key)
    if not isinstance(raw, list):
        raise RuntimeError(f"devops_script_contract.{key} must be a list")
    return tuple(str(item).strip() for item in raw if str(item).strip())


def _manifest_tools() -> dict[str, tuple[str, ...]]:
    raw = _policy().get("manifest_tools")
    if not isinstance(raw, dict):
        raise RuntimeError("devops_script_contract.manifest_tools must be an object")
    result: dict[str, tuple[str, ...]] = {}
    for manifest, tools in raw.items():
        if not isinstance(tools, list):
            continue
        result[str(manifest)] = tuple(
            str(tool).strip() for tool in tools if str(tool).strip()
        )
    return result


def _looks_like_script(rel_path: str) -> bool:
    if not rel_path:
        return False
    name = Path(rel_path).name
    if name in _string_tuple("bare_script_names"):
        return True
    if rel_path.startswith(".github/workflows/"):
        return True
    if rel_path.endswith(_string_tuple("script_suffixes")):
        return True
    if (
        rel_path.endswith(_string_tuple("workflow_suffixes"))
        and "workflow" in rel_path.lower()
    ):
        return True
    return False


def _detected_verbs(text: str) -> tuple[str, ...]:
    if not text:
        return ()
    lowered = text.lower()
    matched: list[str] = []
    for verb in _string_tuple("runnable_verbs"):
        if re.search(rf"\b{re.escape(verb)}\b", lowered):
            matched.append(verb)
    return tuple(matched)


def _candidate_repo_paths(workspace_path: Path) -> set[str]:
    if not workspace_path.is_dir():
        return set()
    found: set[str] = set()
    allowed_hidden = set(_string_tuple("allowed_hidden_root_names"))
    try:
        for entry in workspace_path.iterdir():
            if entry.name.startswith(".") and entry.name not in allowed_hidden:
                continue
            found.add(entry.name)
    except OSError:
        return set()
    for name in _manifest_tools():
        if (workspace_path / name).is_file() and name not in found:
            found.add(name)
    return found


def _candidate_tool_invocations(workspace_path: Path) -> set[str]:
    tools: set[str] = set()
    for manifest, tool_set in _manifest_tools().items():
        if (workspace_path / manifest).is_file():
            tools.update(tool_set)
    return tools


def _references_repo(
    text: str,
    repo_names: Iterable[str],
    tool_invocations: Iterable[str],
) -> bool:
    if not text:
        return False
    for name in repo_names:
        if not name:
            continue
        if re.search(rf"(^|[\s./'\"]){re.escape(name)}(\b|/)", text):
            return True
    for tool in tool_invocations:
        if not tool:
            continue
        if re.search(rf"(^|[\s./'\"]){re.escape(tool)}\b", text):
            return True
    return False


def _read_text_safely(
    workspace_path: Path,
    rel_path: str,
    *,
    max_bytes: int = 64 * 1024,
) -> str:
    candidate = (workspace_path / rel_path).resolve()
    try:
        candidate.relative_to(workspace_path.resolve())
    except (OSError, ValueError):
        return ""
    if not candidate.is_file():
        return ""
    try:
        with candidate.open("rb") as handle:
            payload = handle.read(max_bytes)
    except OSError:
        return ""
    return payload.decode("utf-8", errors="replace")


def evaluate_devops_script_contract(state: PipelineState) -> list[ScriptFinding]:
    workspace_root = str(state.get("workspace_root") or "").strip()
    if not workspace_root:
        return []
    workspace_path = Path(workspace_root)
    workspace_writes = state.get("workspace_writes") or {}
    if not isinstance(workspace_writes, dict):
        return []
    candidate_paths: list[str] = []
    for bucket in ("written", "patched", "udiff_applied"):
        for entry in workspace_writes.get(bucket) or []:
            text = str(entry).strip()
            if text and text not in candidate_paths:
                candidate_paths.append(text)
    if not candidate_paths:
        return []

    repo_names = _candidate_repo_paths(workspace_path)
    tool_invocations = _candidate_tool_invocations(workspace_path)
    findings: list[ScriptFinding] = []
    for rel_path in candidate_paths:
        if not _looks_like_script(rel_path):
            continue
        body = _read_text_safely(workspace_path, rel_path)
        verbs = _detected_verbs(body)
        if not verbs:
            continue
        if _references_repo(body, repo_names, tool_invocations):
            continue
        findings.append(
            ScriptFinding(
                path=rel_path,
                reason=(
                    "script claims runnable verbs but references no repository path; "
                    "expected a reference to one of the workspace files/dirs"
                ),
                verbs=verbs,
            )
        )
    return findings


def enforce_devops_script_contract(state: PipelineState) -> None:
    findings = evaluate_devops_script_contract(state)
    if not findings:
        return
    state_dict = ephemeral_as_dict(state)
    state_dict["devops_script_contract_failures"] = [
        finding.to_dict() for finding in findings
    ]
    unverified = list(
        cast(list[Any], state_dict.get("devops_unverified_claims") or [])
    )
    for finding in findings:
        unverified.append(
            f"unverified script {finding.path}: {finding.reason}"
        )
    state_dict["devops_unverified_claims"] = unverified
    summary = "; ".join(
        f"{finding.path} [verbs={','.join(finding.verbs)}]"
        for finding in findings
    )
    failed_gates = list(state_dict.get("_failed_trusted_gates") or [])
    failed_gates.append("devops_script_contract")
    state_dict["_failed_trusted_gates"] = failed_gates
    existing_summary = str(state_dict.get("_failed_trusted_gates_summary") or "")
    state_dict["_failed_trusted_gates_summary"] = (
        f"{existing_summary}; devops_script_contract: {summary}"
        if existing_summary
        else f"devops_script_contract: {summary}"
    )
    logger.warning(
        "devops_script_contract: %d script(s) failed runnable-claim check: %s",
        len(findings), summary,
    )


__all__ = (
    "ScriptFinding",
    "evaluate_devops_script_contract",
    "enforce_devops_script_contract",
)

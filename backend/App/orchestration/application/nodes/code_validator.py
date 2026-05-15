from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.spec.application.document_graph_service import (
    build_workspace_graph,
    read_graph,
)

logger = logging.getLogger(__name__)

_KIND_TO_GLOB = {  # config-discipline: code-owned
    "schema": ("**/*.py", "**/*.ts", "**/*.tsx"),
    "endpoint": ("**/*.py", "**/*.ts"),
    "pkg": ("**/*.py", "**/*.ts"),
    "policy": ("**/*.md", "**/*.py"),
}


def _workspace_root(state: PipelineState) -> Path | None:
    raw = state.get("workspace_root")
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def _name_from_produces_key(produces_key: str) -> tuple[str, str]:
    parts = produces_key.split(":", 1)
    if len(parts) != 2:
        return ("unknown", produces_key)
    return (parts[0].strip().lower(), parts[1].strip())


def _scan_files(workspace_root: Path, kind: str) -> list[Path]:
    globs = _KIND_TO_GLOB.get(kind, ("**/*",))
    results: list[Path] = []
    for pattern in globs:
        for path in workspace_root.rglob(pattern):
            if not path.is_file():
                continue
            if "_archive" in path.parts or ".swarm" in path.parts:
                continue
            results.append(path)
    return results


def _has_reference(files: list[Path], identifier: str) -> bool:
    needle = identifier.strip()
    if not needle:
        return False
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if needle in text:
            return True
    return False


def code_validator_node(state: PipelineState) -> dict[str, Any]:
    workspace = _workspace_root(state)
    if workspace is None:
        logger.warning(
            "code_validator: no workspace_root in state — skipping validation"
        )
        return {"code_validator_output": "skipped: no workspace_root configured"}
    graph_payload = read_graph(workspace)
    if graph_payload is None:
        graph = build_workspace_graph(workspace)
        graph_payload = graph.to_dict()
    produces_index = graph_payload.get("produces") or {}
    findings: list[dict[str, str]] = []
    files_by_kind: dict[str, list[Path]] = {}
    for produces_key, owning_specs in produces_index.items():
        kind, identifier = _name_from_produces_key(produces_key)
        files = files_by_kind.get(kind)
        if files is None:
            files = _scan_files(workspace, kind)
            files_by_kind[kind] = files
        if not _has_reference(files, identifier):
            findings.append(
                {
                    "kind": kind,
                    "identifier": identifier,
                    "produces_key": produces_key,
                    "owning_specs": ", ".join(owning_specs)
                    if isinstance(owning_specs, list)
                    else str(owning_specs),
                    "severity": "error",
                    "detail": (
                        f"produces key {produces_key!r} has no consumer in workspace files"
                    ),
                }
            )
    verdict = "fail" if any(f["severity"] == "error" for f in findings) else "pass"
    payload = {
        "verdict": verdict,
        "errors": sum(1 for f in findings if f["severity"] == "error"),
        "warnings": sum(1 for f in findings if f["severity"] == "warning"),
        "findings": findings,
    }
    logger.info(
        "code_validator: verdict=%s errors=%d produces_index_size=%d",
        verdict,
        payload["errors"],
        len(produces_index),
    )
    summary_lines = [
        f"verdict: {verdict}",
        f"errors: {payload['errors']}",
        f"warnings: {payload['warnings']}",
    ]
    for finding in findings:
        summary_lines.append(
            f"- [{finding['severity']}] {finding['kind']}:{finding['identifier']} → {finding['detail']}"
        )
    return {
        "code_validator_output": "\n".join(summary_lines),
        "code_validator_report": payload,
    }


__all__ = ["code_validator_node"]

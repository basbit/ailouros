
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, cast

from backend.App.orchestration.domain.contract_validator import normalize_evidence

logger = logging.getLogger(__name__)


def _normalize_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_repo_evidence_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    path = str(raw.get("path") or "").strip()
    excerpt = str(raw.get("excerpt") or "")
    why = str(raw.get("why") or raw.get("explanation") or "").strip()
    start_line = _normalize_int(raw.get("start_line"))
    end_line = _normalize_int(raw.get("end_line"))
    if not path or not why or start_line < 1 or end_line < start_line:
        return None
    normalized = {
        "path": path,
        "start_line": start_line,
        "end_line": end_line,
        "why": why,
    }
    if excerpt.strip():
        normalized["excerpt"] = excerpt.strip()
    return normalized


def _empty_repo_evidence_artifact() -> dict[str, Any]:
    return {"repo_evidence": [], "unverified_claims": [], "has_artifact": False}


def _iter_json_objects(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    results: list[dict[str, Any]] = []
    seen_spans: set[tuple[int, int]] = set()
    for match in re.finditer(r"{", text or ""):
        start = match.start()
        try:
            parsed, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        span = (start, start + end)
        if span in seen_spans:
            continue
        seen_spans.add(span)
        results.append(parsed)
    return results


def _repo_entry_to_transport_evidence(entry: dict[str, Any]) -> dict[str, Any]:
    ref = (
        f"{entry.get('path', '')}:{entry.get('start_line', 0)}-{entry.get('end_line', 0)}"
    ).strip(":")
    return {
        "source": "workspace",
        "kind": "repo_excerpt",
        "ref": ref,
        "data": str(entry.get("excerpt") or ""),
        "why": str(entry.get("why") or ""),
    }


def parse_repo_evidence_artifact(raw_output: str) -> dict[str, Any]:
    text = (raw_output or "").strip()
    if not text:
        return _empty_repo_evidence_artifact()

    fence_contents = [
        match.group(1).strip()
        for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    ]
    candidates: list[dict[str, Any]] = []
    for candidate_text in fence_contents + [text]:
        candidates.extend(_iter_json_objects(candidate_text))

    for data in candidates:
        if "repo_evidence" not in data and "unverified_claims" not in data:
            continue
        evidence_entries = [
            entry
            for item in list(data.get("repo_evidence") or [])
            if (entry := _normalize_repo_evidence_entry(item)) is not None
        ]
        unverified_claims = [
            claim
            for item in list(data.get("unverified_claims") or [])
            if (claim := str(item or "").strip())
        ]
        return {
            "repo_evidence": evidence_entries,
            "unverified_claims": unverified_claims,
            "has_artifact": True,
        }
    return _empty_repo_evidence_artifact()


def _retry_prompt_for_repo_evidence_failure(
    *,
    base_prompt: str,
    raw_output: str,
    step_id: str,
    error: str,
) -> str:
    snippet = (raw_output or "").strip()
    if len(snippet) > 1200:
        snippet = snippet[:1200].rstrip() + "…"
    return (
        base_prompt
        + "\n\n[CRITICAL RETRY] Your previous answer violated the mandatory repo-evidence contract for "
        f"{step_id}. Fix the final canonical ```json``` block so it contains valid `repo_evidence` "
        "and `unverified_claims`.\n"
        f"Validation error: {error}\n"
        "Rules:\n"
        "- `excerpt` must be copied exactly from the referenced workspace lines.\n"
        "- `path`, `start_line`, and `end_line` must point to real lines in the workspace.\n"
        "- If a repository-based claim cannot be proven exactly, move it to `unverified_claims`.\n"
        "- Do not omit the final JSON block on this retry.\n\n"
        "Previous answer excerpt:\n"
        f"{snippet}\n"
    )


def _artifact_only_retry_prompt_for_repo_evidence_failure(
    *,
    raw_output: str,
    step_id: str,
    error: str,
) -> str:
    snippet = (raw_output or "").strip()
    if len(snippet) > 2000:
        snippet = snippet[:2000].rstrip() + "…"
    return (
        "[ARTIFACT-ONLY REPAIR]\n"
        f"Your previous answer for {step_id} is missing or has invalid canonical repo_evidence JSON.\n"
        "Return ONLY one final ```json``` block and nothing else.\n"
        "Schema:\n"
        '{'
        '"repo_evidence":[{"path":"relative/path","start_line":1,"end_line":3,'
        '"excerpt":"exact text copied from the repository","why":"what this proves"}],'
        '"unverified_claims":["claim that cannot be proven from the repository yet"]'
        '}\n'
        f"Validation error: {error}\n"
        "Rules:\n"
        "- Use only repository-backed claims from the previous answer.\n"
        "- `excerpt` must exactly match the referenced workspace lines.\n"
        "- If a repository-based claim cannot be proven exactly, move it to `unverified_claims`.\n"
        "- Return ONLY the final JSON block, with no prose before or after it.\n\n"
        "Previous answer excerpt:\n"
        f"{snippet}\n"
    )


def _read_workspace_excerpt(
    *,
    workspace_root: str,
    rel_path: str,
    start_line: int,
    end_line: int,
) -> str:
    root = Path(workspace_root).expanduser().resolve()
    target = (root / rel_path).resolve()
    if not os.path.commonpath([str(root), str(target)]) == str(root):
        raise RuntimeError(
            f"repo_evidence path escapes workspace: {rel_path!r} under {workspace_root!r}"
        )
    if not target.is_file():
        raise RuntimeError(f"repo_evidence path does not exist in workspace: {rel_path}")
    content = target.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    if start_line > len(lines):
        raise RuntimeError(
            f"repo_evidence start_line out of range for {rel_path}: {start_line} > {len(lines)}"
        )
    excerpt_lines = lines[start_line - 1:end_line]
    return "\n".join(excerpt_lines).strip()


def validate_repo_evidence_against_workspace(
    artifact: dict[str, Any],
    *,
    workspace_root: str,
) -> dict[str, Any]:
    if not artifact.get("has_artifact"):
        raise RuntimeError("missing canonical repo_evidence artifact")
    root = (workspace_root or "").strip()
    repo_evidence = list(artifact.get("repo_evidence") or [])
    unverified_claims = [
        claim for claim in list(artifact.get("unverified_claims") or []) if str(claim).strip()
    ]
    if not repo_evidence and not unverified_claims:
        raise RuntimeError(
            "repo_evidence artifact must contain either repo_evidence[] or unverified_claims[]"
        )
    if not root:
        if repo_evidence:
            raise RuntimeError(
                "repo_evidence validation requires non-empty workspace_root when repo_evidence[] is present"
            )
        return {
            "repo_evidence": [],
            "unverified_claims": unverified_claims,
            "has_artifact": True,
        }

    validated_entries: list[dict[str, Any]] = []
    for entry in repo_evidence:
        rel_path = str(entry.get("path") or "").strip()
        start_line = _normalize_int(entry.get("start_line"))
        end_line = _normalize_int(entry.get("end_line"))
        expected_excerpt = str(entry.get("excerpt") or "").strip()
        actual_excerpt = _read_workspace_excerpt(
            workspace_root=root,
            rel_path=rel_path,
            start_line=start_line,
            end_line=end_line,
        )
        excerpt_sha256 = hashlib.sha256(
            actual_excerpt.encode("utf-8", errors="replace")
        ).hexdigest()
        normalized = dict(entry)
        if expected_excerpt and actual_excerpt != expected_excerpt:
            normalized["model_excerpt"] = expected_excerpt
            normalized["excerpt_repaired"] = True
        normalized["excerpt_sha256"] = excerpt_sha256
        normalized["excerpt"] = actual_excerpt
        normalized_transport = normalize_evidence(cast(Any, _repo_entry_to_transport_evidence(normalized)))
        normalized["transport_evidence"] = normalized_transport
        normalized["hash"] = normalized_transport.get("hash", excerpt_sha256)
        normalized["preview"] = normalized_transport.get("preview", actual_excerpt[:120].replace("\n", " "))
        normalized["size"] = normalized_transport.get("size", len(actual_excerpt.encode("utf-8", errors="replace")))
        validated_entries.append(normalized)

    return {
        "repo_evidence": validated_entries,
        "unverified_claims": unverified_claims,
        "has_artifact": True,
    }


def enforce_repo_evidence_policy(
    artifact: dict[str, Any],
    *,
    workspace_root: str,
    step_id: str,
) -> dict[str, Any]:
    validated = validate_repo_evidence_against_workspace(artifact, workspace_root=workspace_root)
    root = (workspace_root or "").strip()
    unverified_claims = [claim for claim in list(validated.get("unverified_claims") or []) if str(claim).strip()]
    if root and unverified_claims:
        normalized = dict(validated)
        normalized["suppressed_unverified_claims"] = unverified_claims
        normalized["unverified_claims"] = []
        normalized["repo_evidence_policy_warning"] = (
            f"{step_id}: suppressed {len(unverified_claims)} unresolved repo-based claims "
            "because workspace-backed evidence is required"
        )
        return normalized
    return validated


def _repo_evidence_max_retries() -> int:
    raw = os.getenv("SWARM_REPO_EVIDENCE_MAX_RETRIES", "2").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"SWARM_REPO_EVIDENCE_MAX_RETRIES must be an integer, got {raw!r}"
        ) from exc
    if value < 0 or value > 2:
        raise RuntimeError(
            f"SWARM_REPO_EVIDENCE_MAX_RETRIES must be 0, 1, or 2 (got {value})"
        )
    return value


def _empty_unverified_result(raw_output: str) -> tuple[str, dict[str, Any]]:
    return (raw_output or ""), {
        "repo_evidence": [],
        "unverified_claims": [],
        "has_artifact": False,
        "repair_failed": True,
    }


def ensure_validated_repo_evidence(
    *,
    raw_output: str,
    base_prompt: str,
    workspace_root: str,
    step_id: str,
    retry_run: Any,
) -> tuple[str, dict[str, Any]]:
    max_retries = _repo_evidence_max_retries()
    current_output = raw_output
    artifact = parse_repo_evidence_artifact(current_output)
    error_message = ""

    if not artifact.get("has_artifact"):
        error_message = "missing canonical repo_evidence JSON artifact"
    else:
        try:
            return current_output, enforce_repo_evidence_policy(
                artifact,
                workspace_root=workspace_root,
                step_id=step_id,
            )
        except RuntimeError as exc:
            error_message = str(exc)

    if max_retries < 1:
        logger.warning(
            "%s: repo_evidence validation failed and retries are disabled "
            "(SWARM_REPO_EVIDENCE_MAX_RETRIES=0) — continuing with empty evidence",
            step_id,
        )
        return _empty_unverified_result(raw_output)

    retry_prompt = _retry_prompt_for_repo_evidence_failure(
        base_prompt=base_prompt,
        raw_output=current_output,
        step_id=step_id,
        error=error_message or "repo_evidence validation failed",
    )
    current_output = str(retry_run(retry_prompt) or "")
    artifact = parse_repo_evidence_artifact(current_output)
    if artifact.get("has_artifact"):
        try:
            validated = enforce_repo_evidence_policy(
                artifact,
                workspace_root=workspace_root,
                step_id=step_id,
            )
            return current_output, validated
        except RuntimeError as exc:
            error_message = str(exc)
    else:
        error_message = "missing canonical repo_evidence JSON artifact after retry"

    if max_retries < 2:
        logger.warning(
            "%s: repo_evidence still invalid after one retry and artifact-only "
            "repair is disabled (SWARM_REPO_EVIDENCE_MAX_RETRIES=1) — continuing "
            "with empty evidence",
            step_id,
        )
        return _empty_unverified_result(current_output or raw_output)

    artifact_only_prompt = _artifact_only_retry_prompt_for_repo_evidence_failure(
        raw_output=current_output or raw_output,
        step_id=step_id,
        error=error_message or "repo_evidence validation failed after retry",
    )
    artifact_only_output = str(retry_run(artifact_only_prompt) or "")
    repaired_artifact = parse_repo_evidence_artifact(artifact_only_output)
    if not repaired_artifact.get("has_artifact"):
        logger.warning(
            "%s: missing canonical repo_evidence JSON artifact after all repair attempts — "
            "continuing with empty evidence; downstream steps will use unverified claims only",
            step_id,
        )
        return _empty_unverified_result(current_output or raw_output)
    try:
        validated = enforce_repo_evidence_policy(
            repaired_artifact,
            workspace_root=workspace_root,
            step_id=step_id,
        )
    except RuntimeError as exc:
        logger.warning(
            "%s: repo_evidence validation failed after artifact-only repair (%s) — "
            "continuing with empty evidence",
            step_id, exc,
        )
        return _empty_unverified_result(current_output or raw_output)

    final_output = (current_output or raw_output or "").strip()
    repaired_block = artifact_only_output.strip()
    if final_output and repaired_block:
        if repaired_block not in final_output:
            final_output = f"{final_output}\n\n{repaired_block}"
    else:
        final_output = repaired_block or final_output
    return final_output, validated


def format_repo_evidence_for_prompt(artifact: dict[str, Any], *, max_items: int = 8) -> str:
    repo_evidence = list(artifact.get("repo_evidence") or [])
    unverified_claims = list(artifact.get("unverified_claims") or [])
    suppressed_claims = list(artifact.get("suppressed_unverified_claims") or [])
    if not repo_evidence and not unverified_claims and not suppressed_claims:
        return "No structured repo_evidence artifact provided."

    parts: list[str] = []
    if repo_evidence:
        parts.append("Validated repo evidence:")
        for entry in repo_evidence[:max_items]:
            parts.append(
                "- "
                f"{entry['path']}:{entry['start_line']}-{entry['end_line']} "
                f"| sha256={entry['excerpt_sha256']} "
                f"| why={entry['why']}"
            )
    if unverified_claims:
        parts.append("Explicitly unverified claims:")
        for claim in unverified_claims[:max_items]:
            parts.append(f"- {claim}")
    if suppressed_claims:
        parts.append("Suppressed unresolved repo claims (not accepted as validated evidence):")
        for claim in suppressed_claims[:max_items]:
            parts.append(f"- {claim}")
    return "\n".join(parts)


from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

from backend.App.orchestration.domain.gates import (
    _DIFF_DELETED_LINES_THRESHOLD,
    _STUB_PATTERNS,
    DevManifest,
    GateResult,
    StubFinding,
    evaluate_consistency_gate,
    evaluate_diff_risk_gate,
    evaluate_spec_gate,
    evaluate_stub_gate,
)
from backend.App.orchestration.infrastructure.trusted_verification import (
    discover_trusted_commands,
    run_trusted_command,
)
from backend.App.orchestration.infrastructure.verification_workspace import (
    collect_path_existence,
    collect_php_autoload_namespace_findings,
    collect_symbol_presence,
    normalize_workspace_path as _normalize_workspace_path,
    scan_stub_findings,
)

logger = logging.getLogger(__name__)


def run_build_gate(
    workspace_root: str,
    changed_files: Optional[list[str]] = None,
) -> GateResult:
    commands = discover_trusted_commands(workspace_root, changed_files)
    if not commands:
        return GateResult(
            passed=True,
            gate_name="build_gate",
            details={"reason": "no_build_markers_found"},
        )

    errors: list[dict[str, Any]] = []
    all_stdout: list[str] = []
    all_stderr: list[str] = []

    for cmd in commands:
        try:
            returncode, combined_output = run_trusted_command(
                cmd, cwd=workspace_root, timeout_sec=120,
            )
        except FileNotFoundError as exc:
            logger.warning(
                "build_gate: command %s is not on PATH: %s",
                cmd[0], exc,
            )
            errors.append({
                "command": " ".join(cmd),
                "returncode": 127,
                "error": "COMMAND_NOT_FOUND",
                "expected": "trusted verification command is available on PATH",
                "actual": str(exc),
            })
            continue
        all_stdout.append(f"$ {' '.join(cmd)}\n{combined_output}")
        if returncode != 0:
            errors.append({
                "command": " ".join(cmd),
                "returncode": returncode,
                "stderr": "",
                "stdout": combined_output[:1000],
            })

    return GateResult(
        passed=len(errors) == 0,
        gate_name="build_gate",
        errors=errors,
        raw_stdout="\n".join(all_stdout),
        raw_stderr="\n".join(all_stderr),
        details={"commands_run": len(commands), "commands_failed": len(errors)},
    )


def run_spec_gate(
    workspace_root: str,
    manifest: Optional[DevManifest] = None,
    must_exist_files: Optional[list[str]] = None,
) -> GateResult:
    if not workspace_root:
        return GateResult(
            passed=True, gate_name="spec_gate", details={"reason": "no_workspace"},
        )

    manifest_files = (
        list(manifest.new_files) + list(manifest.changed_files) if manifest else []
    )
    evidence = collect_path_existence(
        workspace_root,
        manifest_files=manifest_files,
        must_exist_files=must_exist_files,
    )
    return evaluate_spec_gate(manifest=manifest, path_existence=evidence)


def run_consistency_gate(
    workspace_root: str,
    manifest: Optional[DevManifest] = None,
    spec_symbols: Optional[list[str]] = None,
) -> GateResult:
    symbol_presence = collect_symbol_presence(workspace_root, spec_symbols)
    namespace_findings = collect_php_autoload_namespace_findings(workspace_root)
    return evaluate_consistency_gate(
        symbol_presence=symbol_presence,
        namespace_findings=namespace_findings,
        symbols_checked=len(spec_symbols or []),
    )


def detect_stubs(
    workspace_root: str,
    changed_files: Optional[list[str]] = None,
    allow_list: Optional[list[dict[str, str]]] = None,
    production_paths: Optional[list[str]] = None,
) -> list[StubFinding]:
    raw_findings = scan_stub_findings(
        workspace_root,
        changed_files=changed_files,
        production_paths=production_paths,
        allow_list=allow_list,
        patterns=_STUB_PATTERNS,
    )
    return [
        StubFinding(
            file_path=str(item.get("file_path") or ""),
            line_number=int(item.get("line_number") or 0),
            pattern=str(item.get("pattern") or ""),
            line_content=str(item.get("line_content") or ""),
        )
        for item in raw_findings
    ]


def run_stub_gate(
    workspace_root: str,
    changed_files: Optional[list[str]] = None,
    allow_list: Optional[list[dict[str, str]]] = None,
    production_paths: Optional[list[str]] = None,
) -> GateResult:
    findings = detect_stubs(workspace_root, changed_files, allow_list, production_paths)
    return evaluate_stub_gate(
        findings=findings,
        files_scanned=len(changed_files or []),
        production_paths=list(production_paths or []),
        allow_list_entries=len(allow_list or []),
    )


_TEST_COMMAND_TOKENS = ("test", "pytest", "phpunit", "vitest", "jest", "cargo test")
_DELETION_JUSTIFICATION_TOKENS = ("delete", "delet", "replace", "equivalent", "regression", "justif")
_NUMSTAT_LINE_RE = re.compile(r"^\s*(\d+|-)\s+(\d+|-)\s+(.+?)\s*$")


def run_diff_risk_gate(
    workspace_root: str,
    manifest: Optional[DevManifest] = None,
    workspace_writes: Optional[dict[str, Any]] = None,
) -> GateResult:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not workspace_root:
        return GateResult(passed=True, gate_name="diff_risk_gate")

    workspace_path = Path(workspace_root)
    tracked_paths = (
        manifest.new_files + manifest.changed_files + manifest.deleted_files
        if manifest else []
    )

    has_test_command = False
    has_deletion_justification = False
    rewrite_justifications: dict[str, str] = {}
    newly_created_paths: set[str] = set()

    if manifest:
        for item in manifest.new_files:
            norm = _normalize_workspace_path(workspace_path, item)
            if norm:
                newly_created_paths.add(norm)
        has_test_command = any(
            any(tok in str(cmd.get("command") or "").lower() for tok in _TEST_COMMAND_TOKENS)
            for cmd in manifest.verification_commands
        )
        has_deletion_justification = any(
            any(tok in str(assumption).lower() for tok in _DELETION_JUSTIFICATION_TOKENS)
            for assumption in manifest.assumptions
        )
        for entry in manifest.rewrite_justifications:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path") or "").strip()
            reason = str(entry.get("reason") or "").strip()
            if path and reason:
                rewrite_justifications[
                    _normalize_workspace_path(workspace_path, path)
                ] = reason

    cmd: list[str] = ["git", "diff", "--numstat", "HEAD"]
    if tracked_paths:
        cmd.extend(["--", *tracked_paths])
    try:
        returncode, output = run_trusted_command(cmd, cwd=workspace_root, timeout_sec=30)
    except FileNotFoundError as exc:
        logger.info("diff_risk_gate: git not available on PATH: %s", exc)
        returncode = -1
        output = ""

    if returncode == 0:
        for line in output.strip().split("\n"):
            match = _NUMSTAT_LINE_RE.match(line)
            if not match:
                continue
            deletions_raw = match.group(2)
            filename = match.group(3).strip()
            deletions = int(deletions_raw) if deletions_raw.isdigit() else 0
            if deletions > _DIFF_DELETED_LINES_THRESHOLD:
                warnings.append({
                    "file": filename,
                    "deletions": deletions,
                    "threshold": _DIFF_DELETED_LINES_THRESHOLD,
                    "warning": "LARGE_DELETION",
                    "requires": "test or explicit justification",
                })

    if manifest and manifest.deleted_files:
        for f in manifest.deleted_files:
            warnings.append({
                "file": f,
                "warning": "FILE_DELETED",
                "requires": "replacement or justification in manifest",
            })

    write_actions: list[Any] = []
    if isinstance(workspace_writes, dict):
        actions = workspace_writes.get("write_actions")
        if isinstance(actions, list):
            write_actions = actions
            for entry in actions:
                if not isinstance(entry, dict):
                    continue
                path = _normalize_workspace_path(
                    workspace_path, str(entry.get("path") or ""),
                )
                mode = str(entry.get("mode") or "").strip()
                if path and mode == "create_file":
                    newly_created_paths.add(path)

    for entry in write_actions:
        if not isinstance(entry, dict):
            continue
        path = _normalize_workspace_path(workspace_path, str(entry.get("path") or ""))
        mode = str(entry.get("mode") or "").strip()
        if mode != "overwrite_file" or not path:
            continue
        if path in newly_created_paths:
            warnings.append({"file": path, "warning": "FULL_FILE_REWRITE_ON_NEW_FILE"})
            continue
        if path in rewrite_justifications:
            warnings.append({
                "file": path,
                "warning": "FULL_FILE_REWRITE_JUSTIFIED",
                "justification": rewrite_justifications[path],
            })
            continue
        errors.append({
            "file": path,
            "error": "FULL_FILE_REWRITE_REQUIRES_JUSTIFICATION",
            "expected": (
                "existing file should be edited via patch/search-replace, or full "
                "rewrite must be justified in dev_manifest.rewrite_justifications"
            ),
            "actual": "full file overwrite without explicit structured justification",
        })

    return evaluate_diff_risk_gate(
        errors=errors,
        warnings=warnings,
        has_test_command=has_test_command,
        has_deletion_justification=has_deletion_justification,
        rewrite_justifications=sorted(rewrite_justifications),
        deletion_threshold=_DIFF_DELETED_LINES_THRESHOLD,
    )


def run_all_gates(
    workspace_root: str,
    manifest: Optional[DevManifest] = None,
    must_exist_files: Optional[list[str]] = None,
    spec_symbols: Optional[list[str]] = None,
    production_paths: Optional[list[str]] = None,
    stub_allow_list: Optional[list[dict[str, str]]] = None,
    workspace_writes: Optional[dict[str, Any]] = None,
) -> list[GateResult]:
    return [
        run_build_gate(workspace_root, manifest.changed_files if manifest else None),
        run_spec_gate(workspace_root, manifest, must_exist_files),
        run_consistency_gate(workspace_root, manifest, spec_symbols),
        run_stub_gate(
            workspace_root,
            (manifest.new_files + manifest.changed_files) if manifest else None,
            stub_allow_list,
            production_paths,
        ),
        run_diff_risk_gate(workspace_root, manifest, workspace_writes),
    ]


def gates_passed(results: list[GateResult]) -> bool:
    return all(r.passed for r in results)

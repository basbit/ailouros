"""P0.1/P0.2/P0.6/P1.5/P1.6: Pipeline verification gates.

All gates run in the verification layer (trusted) with raw stdout/stderr.
Gate failure -> pipeline stop. No LLM interpretation of gate results.

Gates:
- BuildGate (P0.1): build/autoload/syntax sanity check
- SpecGate (P0.2): spec->filesystem validation
- ConsistencyGate (P0.6): plan<->implementation consistency
- StubGate (P1.5): placeholder/dummy detection
- DiffRiskGate (P1.6): destructive change protection
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, TypedDict

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

TRUSTED_VERIFICATION_COMMANDS: tuple[str, ...] = (
    "build_gate",
    "spec_gate",
    "consistency_gate",
    "stub_gate",
    "diff_risk_gate",
)
VERIFICATION_RULESET_VERSION = "2026-04-09.v1"

# Stub detection patterns (P1.5)
_STUB_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bFIXME\b", re.IGNORECASE),
    re.compile(r"\bplaceholder\b", re.IGNORECASE),
    re.compile(r"\bdummy\b", re.IGNORECASE),
    re.compile(r"\bfake\b", re.IGNORECASE),
    re.compile(r"\bnot\s+implemented\b", re.IGNORECASE),
    re.compile(r"\bmock[_ ]data\b", re.IGNORECASE),
    re.compile(r"return\s+\[\s*\]"),        # return []
    re.compile(r"return\s+None\b"),         # return None
    re.compile(r"return\s+null\b", re.IGNORECASE),  # return null
    re.compile(r"return\s+\{\s*\}"),        # return {}
    re.compile(r"pass\s*$", re.MULTILINE),  # bare pass
    re.compile(r"raise\s+NotImplementedError"),
]

# Destructive change thresholds (P1.6)
_DIFF_DELETED_LINES_THRESHOLD = int(os.getenv("SWARM_DIFF_DELETED_LINES_THRESHOLD", "50"))


class GateIssue(TypedDict, total=False):
    file: str
    line: int
    pattern: str
    content: str
    error: str
    expected: str
    actual: str
    command: str
    returncode: int
    stderr: str
    stdout: str
    symbol: str
    verification_command: int
    warning: str
    deletions: int
    threshold: int
    requires: str
    justification: str


class GateDetails(TypedDict, total=False):
    verification_ruleset_version: str
    reason: str
    commands_run: int
    commands_failed: int
    manifest_files_checked: int
    must_exist_checked: int
    symbols_checked: int
    autoload_namespace_checks: int
    files_scanned: int
    stubs_found: int
    production_paths: list[str]
    allow_list_entries: int
    deletion_threshold: int
    has_test_command: bool
    has_deletion_justification: bool
    rewrite_justifications: list[str]


@dataclass
class GateResult:
    """Result of a gate check."""

    passed: bool
    gate_name: str
    errors: list[GateIssue] = field(default_factory=list)
    warnings: list[GateIssue] = field(default_factory=list)
    raw_stdout: str = ""
    raw_stderr: str = ""
    details: GateDetails = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        details = dict(self.details)
        details.setdefault("verification_ruleset_version", VERIFICATION_RULESET_VERSION)
        return {
            "passed": self.passed,
            "gate_name": self.gate_name,
            "errors": self.errors,
            "warnings": self.warnings,
            "raw_stdout": self.raw_stdout[:2000],
            "raw_stderr": self.raw_stderr[:2000],
            "details": details,
        }


# ---------------------------------------------------------------------------
# P0.1: Build Gate — project compiles/loads
# ---------------------------------------------------------------------------

def run_build_gate(
    workspace_root: str,
    changed_files: Optional[list[str]] = None,
) -> GateResult:
    """P0.1: Run build/autoload sanity checks.

    Auto-discovers project type and runs appropriate validation commands.
    Fail -> pipeline stop.
    """
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
            returncode, combined_output = run_trusted_command(cmd, cwd=workspace_root, timeout_sec=120)
            all_stdout.append(f"$ {' '.join(cmd)}\n{combined_output}")
            if returncode != 0:
                errors.append({
                    "command": " ".join(cmd),
                    "returncode": returncode,
                    "stderr": "",
                    "stdout": combined_output[:1000],
                })
        except FileNotFoundError:
            logger.debug("build_gate: command not found: %s", cmd[0])
            continue

    return GateResult(
        passed=len(errors) == 0,
        gate_name="build_gate",
        errors=errors,
        raw_stdout="\n".join(all_stdout),
        raw_stderr="\n".join(all_stderr),
        details={"commands_run": len(commands), "commands_failed": len(errors)},
    )


# ---------------------------------------------------------------------------
# P0.2: Spec Gate — spec -> filesystem validation
# ---------------------------------------------------------------------------

@dataclass
class DevManifest:
    """Structured manifest produced by the Dev step.

    Must be included in dev_output for spec gate validation.
    """

    new_files: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    verification_commands: list[dict[str, str]] = field(default_factory=list)
    trusted_verification_commands: list[dict[str, str]] = field(default_factory=list)
    assumptions: list[dict[str, str]] = field(default_factory=list)
    rewrite_justifications: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "new_files": self.new_files,
            "changed_files": self.changed_files,
            "deleted_files": self.deleted_files,
            "verification_commands": self.verification_commands,
            "trusted_verification_commands": self.trusted_verification_commands,
            "assumptions": self.assumptions,
            "rewrite_justifications": self.rewrite_justifications,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DevManifest:
        return cls(
            new_files=list(data.get("new_files") or []),
            changed_files=list(data.get("changed_files") or []),
            deleted_files=list(data.get("deleted_files") or []),
            verification_commands=list(data.get("verification_commands") or []),
            trusted_verification_commands=list(data.get("trusted_verification_commands") or []),
            assumptions=list(data.get("assumptions") or []),
            rewrite_justifications=list(data.get("rewrite_justifications") or []),
        )


def parse_dev_manifest(dev_output: str) -> Optional[DevManifest]:
    """Extract structured manifest from dev output.

    Looks for JSON block between <dev_manifest> tags.
    """
    import json

    match = re.search(
        r"<dev_manifest>\s*(.*?)\s*</dev_manifest>",
        dev_output,
        re.DOTALL,
    )
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        return DevManifest.from_dict(data)
    except (json.JSONDecodeError, TypeError):
        return None


def run_spec_gate(
    workspace_root: str,
    manifest: Optional[DevManifest] = None,
    must_exist_files: Optional[list[str]] = None,
) -> GateResult:
    """P0.2: Validate spec -> filesystem.

    Checks that files declared in the manifest or spec actually exist.
    """
    if not workspace_root:
        return GateResult(passed=True, gate_name="spec_gate", details={"reason": "no_workspace"})

    manifest_files = []

    if manifest:
        manifest_files = list(manifest.new_files) + list(manifest.changed_files)
    evidence = collect_path_existence(
        workspace_root,
        manifest_files=manifest_files,
        must_exist_files=must_exist_files,
    )
    return evaluate_spec_gate(
        manifest=manifest,
        path_existence=evidence,
    )


def evaluate_spec_gate(
    *,
    manifest: Optional[DevManifest] = None,
    path_existence: Optional[dict[str, list[dict[str, Any]]]] = None,
) -> GateResult:
    errors: list[dict[str, Any]] = []
    evidence = path_existence or {"manifest": [], "required": []}
    if manifest:
        for idx, entry in enumerate(manifest.verification_commands):
            command = str(entry.get("command") or "").strip()
            expected = str(entry.get("expected") or "").strip()
            if not command or not expected:
                errors.append({
                    "verification_command": idx,
                    "error": "INVALID_VERIFICATION_COMMAND",
                    "expected": "command and expected are non-empty strings",
                    "actual": entry,
                })
    for entry in evidence["manifest"]:
        if not entry.get("exists"):
            errors.append({
                "file": entry.get("path"),
                "error": "ENOENT",
                "expected": "file exists (declared in dev manifest)",
                "actual": "file not found",
            })
    for entry in evidence["required"]:
        if not entry.get("exists"):
            errors.append({
                "file": entry.get("path"),
                "error": "ENOENT",
                "expected": "file exists (required by spec)",
                "actual": "file not found",
            })
    return GateResult(
        passed=len(errors) == 0,
        gate_name="spec_gate",
        errors=errors,
        details={
            "manifest_files_checked": len(evidence["manifest"]),
            "must_exist_checked": len(evidence["required"]),
        },
    )


# ---------------------------------------------------------------------------
# P0.6: Consistency Gate — plan <-> implementation
# ---------------------------------------------------------------------------

def run_consistency_gate(
    workspace_root: str,
    manifest: Optional[DevManifest] = None,
    spec_symbols: Optional[list[str]] = None,
) -> GateResult:
    """P0.6: Validate plan <-> implementation consistency.

    Checks:
    - Declared files vs actual changes
    - Key symbols/contracts in spec vs actual code
    """
    symbol_presence = collect_symbol_presence(workspace_root, spec_symbols)
    namespace_findings = collect_php_autoload_namespace_findings(workspace_root)
    return evaluate_consistency_gate(
        symbol_presence=symbol_presence,
        namespace_findings=namespace_findings,
        symbols_checked=len(spec_symbols or []),
    )


def evaluate_consistency_gate(
    *,
    symbol_presence: Optional[dict[str, bool]] = None,
    namespace_findings: Optional[list[dict[str, Any]]] = None,
    symbols_checked: int = 0,
) -> GateResult:
    errors: list[dict[str, Any]] = [dict(item) for item in (namespace_findings or [])]
    warnings: list[dict[str, Any]] = []
    for symbol, found in (symbol_presence or {}).items():
        if not found:
            warnings.append({
                "symbol": symbol,
                "error": "SYMBOL_NOT_FOUND",
                "expected": f"symbol '{symbol}' exists in codebase",
                "actual": "not found by grep",
            })
    return GateResult(
        passed=len(errors) == 0,
        gate_name="consistency_gate",
        errors=errors,
        warnings=warnings,
        details={
            "symbols_checked": symbols_checked,
            "autoload_namespace_checks": len(namespace_findings or []),
        },
    )


# ---------------------------------------------------------------------------
# P1.5: Stub Gate — placeholder/dummy detection
# ---------------------------------------------------------------------------

@dataclass
class StubFinding:
    """A detected stub/placeholder in code."""

    file_path: str
    line_number: int
    pattern: str
    line_content: str


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
    """P1.5: Stub detection gate.

    Scans for placeholder logic; findings -> defects.
    """
    findings = detect_stubs(workspace_root, changed_files, allow_list, production_paths)
    return evaluate_stub_gate(
        findings=findings,
        files_scanned=len(changed_files or []),
        production_paths=list(production_paths or []),
        allow_list_entries=len(allow_list or []),
    )


def evaluate_stub_gate(
    *,
    findings: Optional[list[StubFinding]] = None,
    files_scanned: int = 0,
    production_paths: Optional[list[str]] = None,
    allow_list_entries: int = 0,
) -> GateResult:
    errors: list[dict[str, Any]] = []
    for f in findings or []:
        errors.append({
            "file": f.file_path,
            "line": f.line_number,
            "pattern": f.pattern,
            "content": f.line_content,
            "error": "STUB_DETECTED",
        })
    return GateResult(
        passed=len(errors) == 0,
        gate_name="stub_gate",
        errors=errors,
        details={
            "files_scanned": files_scanned,
            "stubs_found": len(findings or []),
            "production_paths": list(production_paths or []),
            "allow_list_entries": allow_list_entries,
        },
    )


# ---------------------------------------------------------------------------
# P1.6: Diff Risk Gate — destructive change protection
# ---------------------------------------------------------------------------

def run_diff_risk_gate(
    workspace_root: str,
    manifest: Optional[DevManifest] = None,
    workspace_writes: Optional[dict[str, Any]] = None,
) -> GateResult:
    """P1.6: Check for destructive changes (large deletions, removed symbols).

    Requires either:
    - A test covering the removed functionality, or
    - An explicit justification in the manifest.
    """
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not workspace_root:
        return GateResult(passed=True, gate_name="diff_risk_gate")

    tracked_paths = manifest.new_files + manifest.changed_files + manifest.deleted_files if manifest else []
    has_test_command = False
    has_deletion_justification = False
    rewrite_justifications: dict[str, str] = {}
    newly_created_paths: set[str] = set()
    if manifest:
        for item in manifest.new_files:
            norm = _normalize_workspace_path(Path(workspace_root), item)
            if norm:
                newly_created_paths.add(norm)
        has_test_command = any(
            any(token in str(cmd.get("command") or "").lower() for token in ("test", "pytest", "phpunit", "vitest", "jest", "cargo test"))
            for cmd in manifest.verification_commands
        )
        has_deletion_justification = any(
            any(token in str(assumption).lower() for token in ("delete", "delet", "replace", "equivalent", "regression", "justif"))
            for assumption in manifest.assumptions
        )
        for entry in manifest.rewrite_justifications:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path") or "").strip()
            reason = str(entry.get("reason") or "").strip()
            if path and reason:
                rewrite_justifications[_normalize_workspace_path(Path(workspace_root), path)] = reason

    # Check git diff for large deletions
    try:
        cmd = ["git", "diff", "--numstat", "HEAD"]
        if tracked_paths:
            cmd.extend(["--", *tracked_paths])
        returncode, output = run_trusted_command(cmd, cwd=workspace_root, timeout_sec=30)
        if returncode == 0:
            # Parse numstat output: added \t deleted \t path
            for line in output.strip().split("\n"):
                match = re.match(r"^\s*(\d+|-)\s+(\d+|-)\s+(.+?)\s*$", line)
                if match:
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
    except FileNotFoundError:
        pass

    # Check manifest deleted_files
    if manifest and manifest.deleted_files:
        for f in manifest.deleted_files:
            warnings.append({
                "file": f,
                "warning": "FILE_DELETED",
                "requires": "replacement or justification in manifest",
            })

    write_actions = workspace_writes.get("write_actions") if isinstance(workspace_writes, dict) else []
    if isinstance(write_actions, list):
        for entry in write_actions:
            if not isinstance(entry, dict):
                continue
            path = _normalize_workspace_path(Path(workspace_root), str(entry.get("path") or ""))
            mode = str(entry.get("mode") or "").strip()
            if path and mode == "create_file":
                newly_created_paths.add(path)
    for entry in write_actions or []:
        if not isinstance(entry, dict):
            continue
        path = _normalize_workspace_path(Path(workspace_root), str(entry.get("path") or ""))
        mode = str(entry.get("mode") or "").strip()
        if mode != "overwrite_file" or not path:
            continue
        if path in newly_created_paths:
            warnings.append({
                "file": path,
                "warning": "FULL_FILE_REWRITE_ON_NEW_FILE",
            })
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
            "expected": "existing file should be edited via patch/search-replace, or full rewrite must be justified in dev_manifest.rewrite_justifications",
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


def evaluate_diff_risk_gate(
    *,
    errors: Optional[list[dict[str, Any]]] = None,
    warnings: Optional[list[dict[str, Any]]] = None,
    has_test_command: bool = False,
    has_deletion_justification: bool = False,
    rewrite_justifications: Optional[list[str]] = None,
    deletion_threshold: int = _DIFF_DELETED_LINES_THRESHOLD,
) -> GateResult:
    out_errors = list(errors or [])
    out_warnings = list(warnings or [])
    out_errors.extend(
        warning for warning in out_warnings
        if not has_test_command and not has_deletion_justification
        and warning.get("warning") not in {"FULL_FILE_REWRITE_JUSTIFIED", "FULL_FILE_REWRITE_ON_NEW_FILE"}
    )
    return GateResult(
        passed=len(out_errors) == 0,
        gate_name="diff_risk_gate",
        errors=out_errors,
        warnings=out_warnings,
        details={
            "deletion_threshold": deletion_threshold,
            "has_test_command": has_test_command,
            "has_deletion_justification": has_deletion_justification,
            "rewrite_justifications": list(rewrite_justifications or []),
        },
    )


# ---------------------------------------------------------------------------
# Composite gate runner
# ---------------------------------------------------------------------------

def run_all_gates(
    workspace_root: str,
    manifest: Optional[DevManifest] = None,
    must_exist_files: Optional[list[str]] = None,
    spec_symbols: Optional[list[str]] = None,
    production_paths: Optional[list[str]] = None,
    stub_allow_list: Optional[list[dict[str, str]]] = None,
    workspace_writes: Optional[dict[str, Any]] = None,
) -> list[GateResult]:
    """Run all verification gates and return results.

    Order: build -> spec -> consistency -> stub -> diff_risk
    """
    results: list[GateResult] = []

    # P0.1
    results.append(run_build_gate(workspace_root, manifest.changed_files if manifest else None))
    # P0.2
    results.append(run_spec_gate(workspace_root, manifest, must_exist_files))
    # P0.6
    results.append(run_consistency_gate(workspace_root, manifest, spec_symbols))
    # P1.5
    results.append(run_stub_gate(
        workspace_root,
        (manifest.new_files + manifest.changed_files) if manifest else None,
        stub_allow_list,
        production_paths,
    ))
    # P1.6
    results.append(run_diff_risk_gate(workspace_root, manifest, workspace_writes))

    return results


def gates_passed(results: list[GateResult]) -> bool:
    """Check if all gates passed."""
    return all(r.passed for r in results)

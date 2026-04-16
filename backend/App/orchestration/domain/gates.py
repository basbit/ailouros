"""Pipeline verification gates — pure domain logic.

This module is the **domain layer**: it defines gate evaluation rules,
data structures, and constants.  It does NOT import from infrastructure
(no I/O, no subprocess, no filesystem) per DDD §10.1.

Orchestration (collecting evidence, calling subprocesses, walking the
filesystem) lives in :mod:`backend.App.orchestration.application.gate_runner`.

Gates:
- BuildGate (P0.1): build/autoload/syntax sanity check
- SpecGate (P0.2): spec→filesystem validation
- ConsistencyGate (P0.6): plan↔implementation consistency
- StubGate (P1.5): placeholder/dummy detection
- DiffRiskGate (P1.6): destructive change protection
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional, TypedDict

TRUSTED_VERIFICATION_COMMANDS: tuple[str, ...] = (
    "build_gate",
    "spec_gate",
    "consistency_gate",
    "stub_gate",
    "diff_risk_gate",
)
VERIFICATION_RULESET_VERSION = "2026-04-09.v1"

# Stub detection patterns (P1.5).  Owned by domain because the rule
# "what counts as a stub" is part of the verification policy.
_STUB_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bFIXME\b", re.IGNORECASE),
    re.compile(r"\bplaceholder\b", re.IGNORECASE),
    re.compile(r"\bdummy\b", re.IGNORECASE),
    re.compile(r"\bfake\b", re.IGNORECASE),
    re.compile(r"\bnot\s+implemented\b", re.IGNORECASE),
    re.compile(r"\bmock[_ ]data\b", re.IGNORECASE),
    re.compile(r"return\s+\[\s*\]"),
    re.compile(r"return\s+None\b"),
    re.compile(r"return\s+null\b", re.IGNORECASE),
    re.compile(r"return\s+\{\s*\}"),
    re.compile(r"pass\s*$", re.MULTILINE),
    re.compile(r"raise\s+NotImplementedError"),
]

# Destructive change threshold (P1.6).  Domain rule, env-tunable.
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
    """Result of a gate check.

    Fields use ``dict[str, Any]`` for ergonomics — callers build heterogeneous
    dicts.  The ``GateIssue`` / ``GateDetails`` TypedDicts above remain as
    human-facing documentation of the expected shapes and may be used by
    callers that want a stricter contract.
    """

    passed: bool
    gate_name: str
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    raw_stdout: str = ""
    raw_stderr: str = ""
    details: dict[str, Any] = field(default_factory=dict)

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
# Dev manifest (domain entity)
# ---------------------------------------------------------------------------

@dataclass
class DevManifest:
    """Structured manifest produced by the Dev step."""

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
            trusted_verification_commands=list(
                data.get("trusted_verification_commands") or []
            ),
            assumptions=list(data.get("assumptions") or []),
            rewrite_justifications=list(data.get("rewrite_justifications") or []),
        )


def parse_dev_manifest(dev_output: str) -> Optional[DevManifest]:
    """Extract structured manifest from dev output.

    Looks for JSON between ``<dev_manifest>`` tags.  Returns ``None`` when
    the tag is absent or the body is not valid JSON.  Callers must treat
    ``None`` as a domain signal ("no manifest declared"), not an error.
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
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return DevManifest.from_dict(data)


# ---------------------------------------------------------------------------
# Stub finding (domain entity)
# ---------------------------------------------------------------------------

@dataclass
class StubFinding:
    """A detected stub/placeholder in code."""

    file_path: str
    line_number: int
    pattern: str
    line_content: str


# ---------------------------------------------------------------------------
# Pure evaluators — operate on collected evidence, no I/O
# ---------------------------------------------------------------------------

def evaluate_spec_gate(
    *,
    manifest: Optional[DevManifest] = None,
    path_existence: Optional[dict[str, list[dict[str, Any]]]] = None,
) -> GateResult:
    """Evaluate spec gate from path-existence evidence."""
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
        and warning.get("warning") not in {
            "FULL_FILE_REWRITE_JUSTIFIED",
            "FULL_FILE_REWRITE_ON_NEW_FILE",
        }
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

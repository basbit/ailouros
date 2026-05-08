from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from backend.App.orchestration.application.enforcement.enforcement_policy import (
    configured_string_list,
    source_integrity_policy,
)
from backend.App.shared.infrastructure.app_config_load import load_app_config_json

logger = logging.getLogger(__name__)


_PATCH_MARKER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("unified_diff_minus", re.compile(r"^---\s+", re.MULTILINE)),
    ("unified_diff_plus", re.compile(r"^\+\+\+\s+", re.MULTILINE)),
    ("unified_diff_hunk", re.compile(r"^@@[^\n]+@@", re.MULTILINE)),
    ("merge_conflict_start", re.compile(r"^<{7}\s", re.MULTILINE)),
    ("merge_conflict_middle", re.compile(r"^={7}\s*$", re.MULTILINE)),
    ("merge_conflict_end", re.compile(r"^>{7}\s", re.MULTILINE)),
)

_FAKE_TOOL_CALL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("tool_call_marker", re.compile(r"<\|tool_call\|?>")),
    ("call_workspace_text", re.compile(r"\bcall:workspace_[a-z_]+\b")),
    ("workspace_edit_file_text", re.compile(r"\bworkspace_edit_file\b")),
    ("workspace_write_file_text", re.compile(r"\bworkspace_write_file\(")),
    ("im_start_tool", re.compile(r"<\|im_start\|>tool_call")),
)


@dataclass(frozen=True)
class CorruptionFinding:
    path: str
    pattern_id: str
    line: int
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _configured_extensions(key: str) -> frozenset[str]:
    extensions = configured_string_list(source_integrity_policy(), key)
    return frozenset(extension.lower() for extension in extensions)


def _configured_int(key: str) -> int:
    value = source_integrity_policy().get(key)
    try:
        parsed_value = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed_value)


def _is_binary_path(path: Path) -> bool:
    return path.suffix.lower() in _configured_extensions("binary_extensions")


def _read_text_safely(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        max_file_bytes = _configured_int("max_file_bytes")
        if max_file_bytes and path.stat().st_size > max_file_bytes:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _line_of(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def _snippet_around(text: str, position: int) -> str:
    start_extra_chars = _configured_int("snippet_start_extra_chars")
    width_chars = _configured_int("snippet_width_chars")
    start = max(0, position - start_extra_chars)
    width = width_chars if width_chars else len(text)
    end = min(len(text), position + width)
    return text[start:end].replace("\n", " ").strip()


def _scan_text(
    relative_path: str,
    text: str,
) -> list[CorruptionFinding]:
    findings: list[CorruptionFinding] = []
    for pattern_id, pattern in _PATCH_MARKER_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        findings.append(
            CorruptionFinding(
                path=relative_path,
                pattern_id=pattern_id,
                line=_line_of(text, match.start()),
                snippet=_snippet_around(text, match.start()),
            )
        )
    for pattern_id, pattern in _FAKE_TOOL_CALL_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        findings.append(
            CorruptionFinding(
                path=relative_path,
                pattern_id=pattern_id,
                line=_line_of(text, match.start()),
                snippet=_snippet_around(text, match.start()),
            )
        )
    return findings


def scan_changed_files(
    workspace_root: Path,
    changed_files: Iterable[str],
) -> list[CorruptionFinding]:
    findings: list[CorruptionFinding] = []
    for relative in changed_files:
        relative_clean = (relative or "").strip().lstrip("/").lstrip("\\")
        if not relative_clean:
            continue
        target = workspace_root / relative_clean
        if _is_binary_path(target):
            continue
        text = _read_text_safely(target)
        if text is None:
            continue
        findings.extend(_scan_text(relative_clean, text))
    return findings


def _ignored_directory_names() -> frozenset[str]:
    try:
        configured = load_app_config_json("workspace_ignored_dirs.json").get(
            "ignored_directory_names",
            [],
        )
    except Exception:
        configured = []
    names = {str(item) for item in configured if str(item).strip()}
    names.update(configured_string_list(source_integrity_policy(), "extra_ignored_directory_names"))
    return frozenset(names)


def _iter_preflight_text_files(
    workspace_root: Path,
    *,
    max_files: int,
) -> Iterable[Path]:
    ignored_dirs = _ignored_directory_names()
    yielded = 0
    stack: list[Path] = [workspace_root]
    while stack and yielded < max_files:
        current = stack.pop()
        try:
            children = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for child in children:
            if yielded >= max_files:
                break
            if child.is_dir():
                if child.name in ignored_dirs:
                    continue
                stack.append(child)
                continue
            if not child.is_file():
                continue
            if child.suffix.lower() not in _configured_extensions("preflight_text_extensions"):
                continue
            if _is_binary_path(child):
                continue
            yielded += 1
            yield child


def scan_workspace_for_source_corruption(
    workspace_root: Path,
    *,
    max_files: int,
) -> list[CorruptionFinding]:
    findings: list[CorruptionFinding] = []
    root = workspace_root.resolve()
    for target in _iter_preflight_text_files(root, max_files=max_files):
        text = _read_text_safely(target)
        if text is None:
            continue
        try:
            relative = target.relative_to(root).as_posix()
        except ValueError:
            relative = target.as_posix()
        findings.extend(_scan_text(relative, text))
    return findings


def scan_agent_output_for_fake_tool_calls(
    agent_output: str,
) -> list[CorruptionFinding]:
    findings: list[CorruptionFinding] = []
    if not isinstance(agent_output, str) or not agent_output:
        return findings
    for pattern_id, pattern in _FAKE_TOOL_CALL_PATTERNS:
        for match in pattern.finditer(agent_output):
            findings.append(
                CorruptionFinding(
                    path="<agent_output>",
                    pattern_id=pattern_id,
                    line=_line_of(agent_output, match.start()),
                    snippet=_snippet_around(agent_output, match.start()),
                )
            )
    return findings


def summarize_findings(findings: list[CorruptionFinding]) -> dict[str, Any]:
    by_path: dict[str, int] = {}
    by_pattern: dict[str, int] = {}
    for finding in findings:
        by_path[finding.path] = by_path.get(finding.path, 0) + 1
        by_pattern[finding.pattern_id] = by_pattern.get(finding.pattern_id, 0) + 1
    return {
        "total": len(findings),
        "by_path": by_path,
        "by_pattern": by_pattern,
    }

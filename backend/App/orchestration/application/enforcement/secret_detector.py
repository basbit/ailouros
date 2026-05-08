from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from backend.App.orchestration.application.enforcement.enforcement_policy import (
    configured_string_list,
    secret_detection_policy,
)

logger = logging.getLogger(__name__)


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_secret_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("slack_token", re.compile(r"xox[abpr]-[A-Za-z0-9-]{20,}")),
    ("pem_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._-]{16,}")),
    ("generic_api_key_assignment", re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|secret[_-]?key|password)\b"
        r"\s*[:=]\s*[\"']?([A-Za-z0-9_./+=-]{12,})"
    )),
)


@dataclass(frozen=True)
class SecretFinding:
    path: str
    pattern_id: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _binary_extensions() -> frozenset[str]:
    return frozenset(
        extension.lower()
        for extension in configured_string_list(secret_detection_policy(), "binary_extensions")
    )


def _max_bytes() -> int:
    try:
        return int(secret_detection_policy().get("max_bytes"))
    except (TypeError, ValueError):
        return 0


def _read_text_safely(path: Path) -> str | None:
    try:
        max_bytes = _max_bytes()
        if not path.is_file() or (max_bytes and path.stat().st_size > max_bytes):
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _line_of(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def scan_paths(
    workspace_root: Path,
    relative_paths: Iterable[str],
) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for relative in relative_paths:
        clean = (relative or "").strip().lstrip("/").lstrip("\\")
        if not clean:
            continue
        target = workspace_root / clean
        if target.suffix.lower() in _binary_extensions():
            continue
        text = _read_text_safely(target)
        if text is None:
            continue
        for pattern_id, pattern in _PATTERNS:
            for match in pattern.finditer(text):
                findings.append(
                    SecretFinding(
                        path=clean,
                        pattern_id=pattern_id,
                        line=_line_of(text, match.start()),
                    )
                )
    return findings


def summarize(findings: list[SecretFinding]) -> dict[str, Any]:
    by_pattern: dict[str, int] = {}
    by_path: dict[str, int] = {}
    for finding in findings:
        by_pattern[finding.pattern_id] = by_pattern.get(finding.pattern_id, 0) + 1
        by_path[finding.path] = by_path.get(finding.path, 0) + 1
    return {
        "total": len(findings),
        "by_pattern": by_pattern,
        "by_path": by_path,
    }

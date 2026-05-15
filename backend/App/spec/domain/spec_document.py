from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal, Optional, cast

SpecStatus = Literal["draft", "reviewed", "stable", "deprecated"]
SpecPrivacy = Literal["public", "internal", "secret"]
SpecComplexity = Literal["low", "medium", "high"]

_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SECTION_HEADER_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_LIST_VALUE_PATTERN = re.compile(r"^\s*-\s*(.+?)\s*$", re.MULTILINE)

_REQUIRED_BODY_SECTIONS: tuple[str, ...] = (
    "Purpose",
    "Public Contract",
    "Behaviour",
)


@dataclass(frozen=True)
class SpecFrontmatter:
    spec_id: str
    version: int = 1
    status: SpecStatus = "draft"
    privacy: SpecPrivacy = "internal"
    hash_inputs: tuple[str, ...] = ()
    codegen_targets: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    last_reviewed_by: Optional[str] = None
    last_reviewed_at: Optional[str] = None
    title: Optional[str] = None
    complexity: SpecComplexity = "medium"
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SpecDocument:
    frontmatter: SpecFrontmatter
    body: str
    sections: tuple[tuple[str, str], ...] = ()

    def section(self, name: str) -> str:
        normalised = name.strip().lower()
        for title, content in self.sections:
            if title.strip().lower() == normalised:
                return content
        return ""

    def codegen_hash(self) -> str:
        parts: list[str] = []
        hash_inputs = self.frontmatter.hash_inputs or _REQUIRED_BODY_SECTIONS
        for section_name in hash_inputs:
            normalised = section_name.replace("_", " ")
            parts.append(self.section(normalised).strip())
        canonical = "\n\n".join(parts)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SpecParseError(ValueError):
    pass


def _parse_scalar(raw: str) -> str:
    stripped = raw.strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        return stripped[1:-1]
    if stripped.startswith("'") and stripped.endswith("'"):
        return stripped[1:-1]
    return stripped


def _parse_inline_list(raw: str) -> tuple[str, ...]:
    inner = raw.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        return ()
    inner = inner[1:-1].strip()
    if not inner:
        return ()
    return tuple(_parse_scalar(item) for item in inner.split(",") if item.strip())


def _parse_frontmatter(text: str) -> tuple[SpecFrontmatter, str]:
    match = _FRONTMATTER_PATTERN.match(text)
    if not match:
        raise SpecParseError("spec is missing YAML frontmatter")
    block = match.group(1)
    body = text[match.end():]

    fields: dict[str, object] = {}
    current_list_key: Optional[str] = None
    for line in block.splitlines():
        if not line.strip():
            current_list_key = None
            continue
        if current_list_key is not None:
            list_match = _LIST_VALUE_PATTERN.match(line)
            if list_match:
                bucket = fields.setdefault(current_list_key, [])
                if isinstance(bucket, list):
                    bucket.append(_parse_scalar(list_match.group(1)))
                continue
            current_list_key = None
        if ":" not in line:
            continue
        key, _, raw_value = line.partition(":")
        key = key.strip()
        value_text = raw_value.strip()
        if not value_text:
            fields[key] = []
            current_list_key = key
            continue
        if value_text.startswith("[") and value_text.endswith("]"):
            fields[key] = list(_parse_inline_list(value_text))
            continue
        fields[key] = _parse_scalar(value_text)

    spec_id = str(fields.get("spec_id") or "").strip()
    if not spec_id:
        raise SpecParseError("spec frontmatter missing required 'spec_id'")

    def _as_tuple(value: object) -> tuple[str, ...]:
        if isinstance(value, list):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return ()

    status_raw = str(fields.get("status") or "draft").strip().lower()
    if status_raw not in {"draft", "reviewed", "stable", "deprecated"}:
        raise SpecParseError(f"invalid status: {status_raw!r}")
    privacy_raw = str(fields.get("privacy") or "internal").strip().lower()
    if privacy_raw not in {"public", "internal", "secret"}:
        raise SpecParseError(f"invalid privacy: {privacy_raw!r}")

    try:
        version = int(str(fields.get("version") or "1"))
    except ValueError:
        version = 1

    complexity_raw = str(fields.get("complexity") or "medium").strip().lower()
    if complexity_raw not in {"low", "medium", "high"}:
        raise SpecParseError(f"invalid complexity: {complexity_raw!r}")

    known_keys = {
        "spec_id", "version", "status", "privacy",
        "hash_inputs", "codegen_targets", "depends_on",
        "last_reviewed_by", "last_reviewed_at", "title", "complexity",
    }
    extra = {
        key: str(value)
        for key, value in fields.items()
        if key not in known_keys and not isinstance(value, list)
    }

    frontmatter = SpecFrontmatter(
        spec_id=spec_id,
        version=version,
        status=cast(SpecStatus, status_raw),
        privacy=cast(SpecPrivacy, privacy_raw),
        hash_inputs=_as_tuple(fields.get("hash_inputs")),
        codegen_targets=_as_tuple(fields.get("codegen_targets")),
        depends_on=_as_tuple(fields.get("depends_on")),
        last_reviewed_by=str(fields.get("last_reviewed_by") or "") or None,
        last_reviewed_at=str(fields.get("last_reviewed_at") or "") or None,
        title=str(fields.get("title") or "") or None,
        complexity=cast(SpecComplexity, complexity_raw),
        extra=extra,
    )
    return frontmatter, body


def _split_sections(body: str) -> tuple[tuple[str, str], ...]:
    matches = list(_SECTION_HEADER_PATTERN.finditer(body))
    if not matches:
        return ()
    pairs: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        content = body[start:end].strip()
        pairs.append((title, content))
    return tuple(pairs)


def parse_spec(text: str) -> SpecDocument:
    frontmatter, body = _parse_frontmatter(text)
    sections = _split_sections(body)
    return SpecDocument(frontmatter=frontmatter, body=body, sections=sections)


def render_spec(document: SpecDocument) -> str:
    frontmatter = document.frontmatter
    lines: list[str] = ["---"]
    lines.append(f"spec_id: {frontmatter.spec_id}")
    lines.append(f"version: {frontmatter.version}")
    lines.append(f"status: {frontmatter.status}")
    lines.append(f"privacy: {frontmatter.privacy}")
    if frontmatter.title:
        lines.append(f'title: "{frontmatter.title}"')
    if frontmatter.hash_inputs:
        lines.append(
            "hash_inputs: ["
            + ", ".join(f'"{item}"' for item in frontmatter.hash_inputs)
            + "]"
        )
    if frontmatter.codegen_targets:
        lines.append("codegen_targets:")
        for target in frontmatter.codegen_targets:
            lines.append(f"  - {target}")
    if frontmatter.depends_on:
        lines.append("depends_on:")
        for dependency in frontmatter.depends_on:
            lines.append(f"  - {dependency}")
    if frontmatter.complexity != "medium":
        lines.append(f"complexity: {frontmatter.complexity}")
    if frontmatter.last_reviewed_by:
        lines.append(f"last_reviewed_by: {frontmatter.last_reviewed_by}")
    if frontmatter.last_reviewed_at:
        lines.append(f"last_reviewed_at: {frontmatter.last_reviewed_at}")
    lines.append("---")
    body = document.body if document.body.startswith("\n") else "\n" + document.body
    return "\n".join(lines) + body


__all__ = [
    "SpecComplexity",
    "SpecDocument",
    "SpecFrontmatter",
    "SpecParseError",
    "SpecPrivacy",
    "SpecStatus",
    "parse_spec",
    "render_spec",
]

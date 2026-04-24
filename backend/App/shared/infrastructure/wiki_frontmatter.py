from __future__ import annotations

import re
from typing import Any

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FRONTMATTER_STRIP_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def strip_frontmatter(text: str) -> str:
    match = _FRONTMATTER_STRIP_RE.match(text)
    return text[match.end():] if match else text


def parse_frontmatter(text: str) -> dict[str, Any]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    result: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            result[key] = (
                [v.strip().strip('"').strip("'") for v in inner.split(",") if v.strip()]
                if inner
                else []
            )
        else:
            result[key] = value.strip('"').strip("'")
    return result


def parse_frontmatter_tags(text: str) -> list[str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return []
    for line in match.group(1).splitlines():
        if not line.startswith("tags:"):
            continue
        value = line[len("tags:"):].strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [v.strip().strip('"').strip("'") for v in inner.split(",") if v.strip()]
    return []


def parse_frontmatter_links(text: str) -> list[str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return []
    for line in match.group(1).splitlines():
        if not line.startswith("links:"):
            continue
        value = line[len("links:"):].strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [v.strip().strip('"').strip("'") for v in inner.split(",") if v.strip()]
    return []


def extract_body_text(text: str) -> str:
    match = _FRONTMATTER_RE.match(text)
    return text[match.end():] if match else text


def extract_body_wiki_links(text: str) -> list[str]:
    body = extract_body_text(text)
    return [target.strip() for target in _WIKI_LINK_RE.findall(body)]


def extract_all_wiki_links(text: str) -> list[str]:
    return parse_frontmatter_links(text) + extract_body_wiki_links(text)

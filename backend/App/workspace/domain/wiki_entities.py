from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class WikiEntity:
    name: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "source": self.source}


def extract_wiki_entities(title: str, tags: list[str], body: str) -> list[WikiEntity]:
    candidates: dict[str, WikiEntity] = {}

    def add_entity(raw_name: str, source: str) -> None:
        name = raw_name.strip().strip("`*_# ")
        if len(name) < 3:
            return
        key = name.lower()
        candidates.setdefault(key, WikiEntity(name=name, source=source))

    add_entity(title, "title")
    for tag in tags:
        add_entity(tag, "tag")
    for heading in _HEADING_PATTERN.findall(body):
        add_entity(heading, "heading")
    for token in _WORD_PATTERN.findall(title):
        if token[:1].isupper():
            add_entity(token, "title_token")

    return [candidates[key] for key in sorted(candidates)]


def build_wiki_entity_index(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    entries: dict[str, list[str]] = {}
    for node in nodes:
        node_id = str(node.get("id") or "")
        for entity in node.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            name = str(entity.get("name") or "").strip()
            if not name:
                continue
            entries.setdefault(name.lower(), []).append(node_id)
    return {
        "schema": "swarm_wiki_entity_index/v1",
        "entities": {
            name: sorted(set(node_ids))
            for name, node_ids in sorted(entries.items())
        },
    }

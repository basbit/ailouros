from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.App.shared.infrastructure.wiki_frontmatter import (
    extract_body_text,
    extract_body_wiki_links,
    parse_frontmatter,
)
from backend.App.workspace.domain.wiki_entities import build_wiki_entity_index, extract_wiki_entities

logger = logging.getLogger(__name__)


def _node_id(wiki_root: Path, path: Path) -> str:
    return path.relative_to(wiki_root).with_suffix("").as_posix()


_PIPELINE_STEP_EDGES: list[tuple[str, str]] = [
    ("planning/ba-output",        "planning/pm-output"),
    ("architecture/arch-output",  "planning/ba-output"),
    ("architecture/arch-output",  "planning/pm-output"),
    ("planning/ba-arch-debate",   "planning/ba-output"),
    ("planning/ba-arch-debate",   "architecture/arch-output"),
    ("planning/spec-merge",       "planning/ba-output"),
    ("planning/spec-merge",       "architecture/arch-output"),
    ("analysis/code-analysis",    "architecture/arch-output"),
    ("documentation/diagrams",    "analysis/code-analysis"),
    ("documentation/docs",        "analysis/code-analysis"),
    ("documentation/docs",        "documentation/diagrams"),
    ("analysis/problem-spotter",  "analysis/code-analysis"),
    ("analysis/refactor-plan",    "analysis/problem-spotter"),
    ("development/dev-lead-plan", "architecture/arch-output"),
    ("development/dev-lead-plan", "planning/ba-output"),
    ("development/dev-output",    "development/dev-lead-plan"),
    ("development/dev-output",    "architecture/arch-output"),
    ("qa/qa-report",              "development/dev-output"),
    ("devops/deployment",         "architecture/arch-output"),
    ("design/ux-research",        "planning/spec-merge"),
    ("design/ux-architecture",    "design/ux-research"),
    ("design/ux-architecture",    "planning/spec-merge"),
    ("design/ui-design",          "design/ux-architecture"),
    ("design/ui-design",          "design/ux-research"),
    ("marketing/seo-strategy",    "development/dev-output"),
    ("marketing/seo-strategy",    "qa/qa-report"),
    ("marketing/ai-citation",     "marketing/seo-strategy"),
    ("marketing/app-store-aso",   "marketing/seo-strategy"),
]

_TAG_COLORS: dict[str, str] = {
    "architecture": "#4dabf7",
    "planning": "#fcc419",
    "development": "#51cf66",
    "analysis": "#ff922b",
    "documentation": "#20c997",
    "qa": "#ff6b6b",
    "devops": "#cc5de8",
    "design": "#e599f7",
    "marketing": "#74c0fc",
    "feature": "#51cf66",
    "api": "#cc5de8",
    "agents": "#ff922b",
    "log": "#868e96",
    "glossary": "#ffd43b",
    "index": "#f06595",
}
_DEFAULT_COLOR = "#adb5bd"


def _primary_tag(tags: list[str]) -> str:
    for tag in tags:
        if tag in _TAG_COLORS:
            return tag
    return ""


def build_wiki_graph(wiki_root: Path) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    edge_set: set[tuple[str, str]] = set()

    md_files = sorted(wiki_root.rglob("*.md"))
    if not md_files:
        return {"nodes": [], "edges": []}

    file_data: dict[str, dict[str, Any]] = {}
    for path in md_files:
        node_id = _node_id(wiki_root, path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("wiki_service: could not read %s: %s", path, exc)
            continue
        frontmatter = parse_frontmatter(text)
        tags: list[str] = frontmatter.get("tags") or []
        title: str = str(frontmatter.get("title") or node_id.replace("/", " / ").title())
        body = extract_body_text(text)
        all_links: list[str] = (frontmatter.get("links") or []) + extract_body_wiki_links(text)
        entities = [entity.to_dict() for entity in extract_wiki_entities(title, tags, body)]
        file_data[node_id] = {"title": title, "tags": tags, "links": all_links, "entities": entities}

    incoming_count: dict[str, int] = {node_id: 0 for node_id in file_data}
    for data in file_data.values():
        for target in data["links"]:
            if target in incoming_count:
                incoming_count[target] += 1

    for node_id, data in sorted(file_data.items()):
        primary_tag = _primary_tag(data["tags"])
        nodes.append({
            "id": node_id,
            "title": data["title"],
            "tags": data["tags"],
            "entities": data["entities"],
            "color": _TAG_COLORS.get(primary_tag, _DEFAULT_COLOR),
            "size": max(8, min(30, 8 + incoming_count[node_id] * 4)),
        })

    for node_id, data in file_data.items():
        for target in data["links"]:
            if target not in file_data:
                continue
            key = (node_id, target)
            if key in edge_set:
                continue
            edge_set.add(key)
            edges.append({"id": f"{node_id}--{target}", "source": node_id, "target": target})

    for source, target in _PIPELINE_STEP_EDGES:
        if source in file_data and target in file_data:
            key = (source, target)
            if key not in edge_set:
                edge_set.add(key)
                edges.append({"id": f"{source}--{target}", "source": source, "target": target})

    return {"nodes": nodes, "edges": edges, "entity_index": build_wiki_entity_index(nodes)}


def resolve_wiki_root(workspace_root: str | None) -> Path:
    if workspace_root and workspace_root.strip():
        root = Path(workspace_root).resolve()
        swarm_wiki = root / ".swarm" / "wiki"
        return swarm_wiki
    from backend.App.paths import APP_ROOT
    return APP_ROOT.parent / "wiki"


def rebuild_and_cache_graph(wiki_root: Path) -> dict[str, Any]:
    graph = build_wiki_graph(wiki_root)
    graph_file = wiki_root / "graph.json"
    try:
        wiki_root.mkdir(parents=True, exist_ok=True)
        graph_file.write_text(
            json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (wiki_root / "entity_index.json").write_text(
            json.dumps(graph.get("entity_index", {}), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "wiki_service: graph rebuilt (%d nodes, %d edges) -> %s",
            len(graph["nodes"]),
            len(graph["edges"]),
            graph_file,
        )
    except OSError as exc:
        logger.warning("wiki_service: could not save graph.json: %s", exc)
    return graph


def read_wiki_file(wiki_root: Path, relative_path: str) -> str:
    clean = relative_path.strip("/").replace("..", "")
    candidates = [
        wiki_root / clean,
        wiki_root / f"{clean}.md",
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(wiki_root.resolve())
        except ValueError:
            raise ValueError(f"path traversal detected: {relative_path!r}")
        if resolved.is_file():
            return resolved.read_text(encoding="utf-8")
    raise FileNotFoundError(f"wiki file not found: {relative_path!r}")


def get_or_build_graph(wiki_root: Path) -> dict[str, Any]:
    if not wiki_root.exists():
        return {"nodes": [], "edges": []}

    graph_file = wiki_root / "graph.json"
    md_files = list(wiki_root.rglob("*.md"))

    if graph_file.exists() and md_files:
        graph_mtime = graph_file.stat().st_mtime
        stale = any(file.stat().st_mtime > graph_mtime for file in md_files)
        if not stale:
            try:
                data = json.loads(graph_file.read_text(encoding="utf-8"))
                logger.debug(
                    "wiki_service: serving cached graph.json (%d nodes)",
                    len(data.get("nodes", [])),
                )
                return data
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("wiki_service: cached graph.json unreadable (%s), rebuilding", exc)

    graph = build_wiki_graph(wiki_root)
    try:
        wiki_root.mkdir(parents=True, exist_ok=True)
        graph_file.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
        (wiki_root / "entity_index.json").write_text(
            json.dumps(graph.get("entity_index", {}), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "wiki_service: graph built and saved (%d nodes, %d edges) -> %s",
            len(graph["nodes"]),
            len(graph["edges"]),
            graph_file,
        )
    except OSError as exc:
        logger.warning("wiki_service: could not save graph.json: %s", exc)

    return graph

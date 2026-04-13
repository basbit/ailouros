"""Wiki graph service — builds graph.json from .swarm/wiki/ markdown files."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inline parsing utilities (ported from scripts/wiki/_wiki_utils.py)
# ---------------------------------------------------------------------------

_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Extract YAML-like frontmatter into a dict (no PyYAML required)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    result: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            result[key] = (
                [
                    v.strip().strip('"').strip("'")
                    for v in inner.split(",")
                    if v.strip()
                ]
                if inner
                else []
            )
        else:
            result[key] = value.strip('"').strip("'")
    return result


def _node_id(wiki_root: Path, path: Path) -> str:
    """Return relative path without .md extension as a POSIX node id."""
    return path.relative_to(wiki_root).with_suffix("").as_posix()


def _body_links(text: str) -> list[str]:
    """Return [[link]] targets found in the body (after frontmatter)."""
    m = _FRONTMATTER_RE.match(text)
    body = text[m.end():] if m else text
    return [t.strip() for t in _WIKI_LINK_RE.findall(body)]


# ---------------------------------------------------------------------------
# Graph building
# ---------------------------------------------------------------------------

# Known pipeline step dependencies — used to inject implicit edges when
# articles don't have explicit `links:` in frontmatter (e.g. old articles).
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
    # Design phase
    ("design/ux-research",        "planning/spec-merge"),
    ("design/ux-architecture",    "design/ux-research"),
    ("design/ux-architecture",    "planning/spec-merge"),
    ("design/ui-design",          "design/ux-architecture"),
    ("design/ui-design",          "design/ux-research"),
    # Marketing phase
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
    """Parse all .md files in wiki_root, return D3-compatible graph dict."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    edge_set: set[tuple[str, str]] = set()

    md_files = sorted(wiki_root.rglob("*.md"))
    if not md_files:
        return {"nodes": [], "edges": []}

    file_data: dict[str, dict[str, Any]] = {}
    for path in md_files:
        nid = _node_id(wiki_root, path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("wiki_service: could not read %s: %s", path, exc)
            continue
        fm = _parse_frontmatter(text)
        tags: list[str] = fm.get("tags") or []
        title: str = str(fm.get("title") or nid.replace("/", " / ").title())
        all_links: list[str] = (fm.get("links") or []) + _body_links(text)
        file_data[nid] = {"title": title, "tags": tags, "links": all_links}

    incoming_count: dict[str, int] = {nid: 0 for nid in file_data}
    for data in file_data.values():
        for target in data["links"]:
            if target in incoming_count:
                incoming_count[target] += 1

    for nid, data in sorted(file_data.items()):
        primary_tag = _primary_tag(data["tags"])
        nodes.append({
            "id": nid,
            "title": data["title"],
            "tags": data["tags"],
            "color": _TAG_COLORS.get(primary_tag, _DEFAULT_COLOR),
            "size": max(8, min(30, 8 + incoming_count[nid] * 4)),
        })

    for nid, data in file_data.items():
        for target in data["links"]:
            if target not in file_data:
                continue
            key = (nid, target)
            if key in edge_set:
                continue
            edge_set.add(key)
            edges.append({"id": f"{nid}--{target}", "source": nid, "target": target})

    # Inject implicit pipeline dependency edges (covers articles without
    # explicit `links:` in frontmatter — e.g. articles from older runs).
    for source, target in _PIPELINE_STEP_EDGES:
        if source in file_data and target in file_data:
            key = (source, target)
            if key not in edge_set:
                edge_set.add(key)
                edges.append({"id": f"{source}--{target}", "source": source, "target": target})

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Cache-aware entry point
# ---------------------------------------------------------------------------

def get_or_build_graph(wiki_root: Path) -> dict[str, Any]:
    """Return cached graph.json if up-to-date, else rebuild and save it.

    graph.json is considered stale if any .md file in wiki_root is newer than it.
    Returns empty graph {"nodes": [], "edges": []} if wiki_root doesn't exist.
    """
    if not wiki_root.exists():
        return {"nodes": [], "edges": []}

    graph_file = wiki_root / "graph.json"

    # Collect all .md files to check freshness
    md_files = list(wiki_root.rglob("*.md"))

    if graph_file.exists() and md_files:
        graph_mtime = graph_file.stat().st_mtime
        stale = any(f.stat().st_mtime > graph_mtime for f in md_files)
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

    # Build (or rebuild) and save
    graph = build_wiki_graph(wiki_root)
    try:
        wiki_root.mkdir(parents=True, exist_ok=True)
        graph_file.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(
            "wiki_service: graph built and saved (%d nodes, %d edges) -> %s",
            len(graph["nodes"]),
            len(graph["edges"]),
            graph_file,
        )
    except OSError as exc:
        logger.warning("wiki_service: could not save graph.json: %s", exc)

    return graph

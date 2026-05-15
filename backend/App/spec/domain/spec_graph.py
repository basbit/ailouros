from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

from backend.App.spec.domain.spec_document import SpecDocument

NodeKind = Literal["spec", "code", "test", "prompt"]
EdgeKind = Literal["depends_on", "generates", "references"]


@dataclass(frozen=True)
class GraphNode:
    id: str
    kind: NodeKind
    payload: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdge:
    from_id: str
    to_id: str
    kind: EdgeKind


@dataclass(frozen=True)
class SpecGraph:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "nodes": [
                {"id": node.id, "kind": node.kind, "payload": dict(node.payload)}
                for node in self.nodes
            ],
            "edges": [
                {"from": edge.from_id, "to": edge.to_id, "kind": edge.kind}
                for edge in self.edges
            ],
        }


_TEST_PATH_FRAGMENTS = ("/tests/", "/test_", "/__tests__/")


def _classify_codegen_target(path: str) -> NodeKind:
    lower = path.lower()
    for fragment in _TEST_PATH_FRAGMENTS:
        if fragment in lower or lower.startswith("tests/"):
            return "test"
    if lower.endswith(".md") and "/prompts/" in lower:
        return "prompt"
    return "code"


def build_graph(documents: Iterable[SpecDocument]) -> SpecGraph:
    nodes: dict[tuple[str, str], GraphNode] = {}
    edges: list[GraphEdge] = []

    def _register_node(node: GraphNode) -> None:
        key = (node.kind, node.id)
        if key not in nodes:
            nodes[key] = node

    for document in documents:
        spec_id = document.frontmatter.spec_id
        _register_node(
            GraphNode(
                id=spec_id,
                kind="spec",
                payload={
                    "status": document.frontmatter.status,
                    "privacy": document.frontmatter.privacy,
                    "version": str(document.frontmatter.version),
                    "title": str(document.frontmatter.title or ""),
                },
            )
        )

    for document in documents:
        spec_id = document.frontmatter.spec_id
        for dependency in document.frontmatter.depends_on:
            edges.append(
                GraphEdge(from_id=spec_id, to_id=dependency, kind="depends_on")
            )
        for target in document.frontmatter.codegen_targets:
            target_kind = _classify_codegen_target(target)
            _register_node(
                GraphNode(id=target, kind=target_kind, payload={"path": target})
            )
            edges.append(
                GraphEdge(from_id=spec_id, to_id=target, kind="generates")
            )

    return SpecGraph(nodes=tuple(nodes.values()), edges=tuple(edges))


def ancestors(graph: SpecGraph, spec_id: str, *, depth: int = 1) -> tuple[str, ...]:
    direct = {edge.to_id for edge in graph.edges if edge.kind == "depends_on" and edge.from_id == spec_id}
    visited: set[str] = set(direct)
    frontier = set(direct)
    for _ in range(max(0, depth - 1)):
        next_frontier: set[str] = set()
        for node in frontier:
            for edge in graph.edges:
                if edge.kind == "depends_on" and edge.from_id == node and edge.to_id not in visited:
                    next_frontier.add(edge.to_id)
                    visited.add(edge.to_id)
        if not next_frontier:
            break
        frontier = next_frontier
    return tuple(sorted(visited))


def dependants(graph: SpecGraph, spec_id: str, *, depth: int = 1) -> tuple[str, ...]:
    direct = {edge.from_id for edge in graph.edges if edge.kind == "depends_on" and edge.to_id == spec_id}
    visited: set[str] = set(direct)
    frontier = set(direct)
    for _ in range(max(0, depth - 1)):
        next_frontier: set[str] = set()
        for node in frontier:
            for edge in graph.edges:
                if edge.kind == "depends_on" and edge.to_id == node and edge.from_id not in visited:
                    next_frontier.add(edge.from_id)
                    visited.add(edge.from_id)
        if not next_frontier:
            break
        frontier = next_frontier
    return tuple(sorted(visited))


def orphan_specs(graph: SpecGraph, *, anchor: str = "_project") -> tuple[str, ...]:
    spec_ids = {node.id for node in graph.nodes if node.kind == "spec"}
    if anchor not in spec_ids:
        return tuple(sorted(spec_ids))
    reachable: set[str] = {anchor}
    frontier = {anchor}
    while frontier:
        next_frontier: set[str] = set()
        for node_id in frontier:
            for edge in graph.edges:
                if edge.kind != "depends_on":
                    continue
                if edge.to_id == node_id and edge.from_id not in reachable:
                    next_frontier.add(edge.from_id)
                    reachable.add(edge.from_id)
                if edge.from_id == node_id and edge.to_id not in reachable:
                    next_frontier.add(edge.to_id)
                    reachable.add(edge.to_id)
        frontier = next_frontier
    return tuple(sorted(spec_ids - reachable))


__all__ = [
    "EdgeKind",
    "GraphEdge",
    "GraphNode",
    "NodeKind",
    "SpecGraph",
    "ancestors",
    "build_graph",
    "dependants",
    "orphan_specs",
]

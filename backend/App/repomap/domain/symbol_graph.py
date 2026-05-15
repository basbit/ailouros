from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SymbolNode:
    file_path: str
    kind: Literal["function", "class", "method"]
    name: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class SymbolEdge:
    from_node_path: str
    to_node_path: str
    weight: float


@dataclass(frozen=True)
class SymbolGraph:
    nodes: tuple[SymbolNode, ...]
    edges: tuple[SymbolEdge, ...]


def compute_page_rank(
    graph: SymbolGraph,
    *,
    damping: float = 0.85,
    iterations: int = 30,
) -> dict[str, float]:
    if iterations < 1:
        raise ValueError(f"iterations must be >= 1, got {iterations}")

    file_paths = list({n.file_path for n in graph.nodes})
    if not file_paths:
        return {}

    n = len(file_paths)
    idx = {fp: i for i, fp in enumerate(file_paths)}
    scores = [1.0 / n] * n

    out_edges: dict[int, list[tuple[int, float]]] = {i: [] for i in range(n)}
    for edge in graph.edges:
        src = idx.get(edge.from_node_path)
        dst = idx.get(edge.to_node_path)
        if src is None or dst is None or src == dst:
            continue
        out_edges[src].append((dst, edge.weight))

    out_weight_totals = [
        sum(w for _, w in targets) for targets in out_edges.values()
    ]

    for _ in range(iterations):
        new_scores = [(1.0 - damping) / n] * n
        for src_i, targets in out_edges.items():
            total = out_weight_totals[src_i]
            if total == 0.0:
                for dst_i in range(n):
                    new_scores[dst_i] += damping * scores[src_i] / n
            else:
                for dst_i, w in targets:
                    new_scores[dst_i] += damping * scores[src_i] * (w / total)
        scores = new_scores

    return {fp: scores[idx[fp]] for fp in file_paths}

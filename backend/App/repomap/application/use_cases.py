from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Optional

from backend.App.repomap.domain.repo_map import RepoMap, RepoMapEntry, render_text
from backend.App.repomap.domain.symbol_graph import SymbolGraph, compute_page_rank
from backend.App.repomap.infrastructure.treesitter_extractor import (
    extract_symbols,
    signatures_by_file,
)

_DEFAULT_MAX_TOKENS = 2048


def _personalised_page_rank(
    graph: SymbolGraph,
    focus_path: str,
    *,
    damping: float = 0.85,
    iterations: int = 30,
    focus_weight: float = 10.0,
) -> dict[str, float]:
    from backend.App.repomap.domain.symbol_graph import SymbolEdge, SymbolGraph

    boosted_edges = list(graph.edges)
    focus_files = {n.file_path for n in graph.nodes if n.file_path == focus_path}
    for node in graph.nodes:
        if node.file_path != focus_path and focus_files:
            boosted_edges.append(
                SymbolEdge(
                    from_node_path=focus_path,
                    to_node_path=node.file_path,
                    weight=focus_weight,
                )
            )
    boosted = SymbolGraph(nodes=graph.nodes, edges=tuple(boosted_edges))
    return compute_page_rank(boosted, damping=damping, iterations=iterations)


def build_repo_map(
    workspace_root: Path,
    focus_path: Optional[Path] = None,
) -> RepoMap:
    graph = extract_symbols(workspace_root)

    if not graph.nodes:
        return RepoMap(entries=())

    if focus_path is not None:
        try:
            rel_focus = focus_path.relative_to(workspace_root).as_posix()
        except ValueError:
            rel_focus = focus_path.as_posix()
        ranks = _personalised_page_rank(graph, rel_focus)
    else:
        ranks = compute_page_rank(graph)

    sigs_by_file = signatures_by_file(graph.nodes)

    entries: list[RepoMapEntry] = []
    for file_path, sigs in sigs_by_file.items():
        entries.append(
            RepoMapEntry(
                file_path=file_path,
                signatures=tuple(sigs),
                rank=ranks.get(file_path, 0.0),
            )
        )

    entries.sort(key=lambda e: e.rank, reverse=True)
    return RepoMap(entries=tuple(entries))


def serve_for_codegen(
    workspace_root: Path,
    focus_path: Optional[Path],
    *,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    token_counter: Optional[Callable[[str], int]] = None,
) -> str:
    repo_map = build_repo_map(workspace_root, focus_path)
    return render_text(repo_map, max_tokens=max_tokens, token_counter=token_counter)

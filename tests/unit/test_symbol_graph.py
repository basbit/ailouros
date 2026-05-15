from __future__ import annotations

import math
import pytest

from backend.App.repomap.domain.symbol_graph import (
    SymbolEdge,
    SymbolGraph,
    SymbolNode,
    compute_page_rank,
)


def _node(file_path: str) -> SymbolNode:
    return SymbolNode(file_path=file_path, kind="function", name="f", line_start=1, line_end=2)


def _edge(src: str, dst: str, weight: float = 1.0) -> SymbolEdge:
    return SymbolEdge(from_node_path=src, to_node_path=dst, weight=weight)


def test_empty_graph_returns_empty_dict():
    g = SymbolGraph(nodes=(), edges=())
    assert compute_page_rank(g) == {}


def test_single_node_sums_to_one():
    g = SymbolGraph(nodes=(_node("a.py"),), edges=())
    ranks = compute_page_rank(g)
    assert math.isclose(sum(ranks.values()), 1.0, abs_tol=1e-6)


def test_two_node_symmetric_graph_equal_ranks():
    g = SymbolGraph(
        nodes=(_node("a.py"), _node("b.py")),
        edges=(_edge("a.py", "b.py"), _edge("b.py", "a.py")),
    )
    ranks = compute_page_rank(g)
    assert math.isclose(sum(ranks.values()), 1.0, abs_tol=1e-6)
    assert math.isclose(ranks["a.py"], ranks["b.py"], abs_tol=1e-4)


def test_hub_ranks_higher_than_leaf():
    nodes = tuple(_node(f"{c}.py") for c in "abcd")
    edges = (
        _edge("a.py", "hub.py"),
        _edge("b.py", "hub.py"),
        _edge("c.py", "hub.py"),
        _edge("d.py", "hub.py"),
    )
    hub = _node("hub.py")
    g = SymbolGraph(nodes=nodes + (hub,), edges=edges)
    ranks = compute_page_rank(g)
    assert ranks["hub.py"] > ranks["a.py"]


def test_ranks_sum_to_one_on_star_graph():
    hub = _node("hub.py")
    leaves = tuple(_node(f"leaf{i}.py") for i in range(5))
    edges = tuple(_edge(f"leaf{i}.py", "hub.py") for i in range(5))
    g = SymbolGraph(nodes=(hub,) + leaves, edges=edges)
    ranks = compute_page_rank(g)
    assert math.isclose(sum(ranks.values()), 1.0, abs_tol=1e-5)


def test_iterations_less_than_one_raises():
    g = SymbolGraph(nodes=(_node("a.py"),), edges=())
    with pytest.raises(ValueError, match="iterations"):
        compute_page_rank(g, iterations=0)


def test_self_loops_ignored():
    g = SymbolGraph(
        nodes=(_node("a.py"),),
        edges=(_edge("a.py", "a.py"),),
    )
    ranks = compute_page_rank(g)
    assert math.isclose(sum(ranks.values()), 1.0, abs_tol=1e-6)


def test_damping_one_distributes_purely_by_links():
    g = SymbolGraph(
        nodes=(_node("a.py"), _node("b.py")),
        edges=(_edge("a.py", "b.py"),),
    )
    ranks = compute_page_rank(g, damping=1.0, iterations=50)
    assert math.isclose(sum(ranks.values()), 1.0, abs_tol=1e-5)


def test_unknown_edge_endpoints_not_present_in_nodes_are_skipped():
    g = SymbolGraph(
        nodes=(_node("a.py"),),
        edges=(_edge("a.py", "ghost.py"),),
    )
    ranks = compute_page_rank(g)
    assert "ghost.py" not in ranks
    assert math.isclose(sum(ranks.values()), 1.0, abs_tol=1e-6)

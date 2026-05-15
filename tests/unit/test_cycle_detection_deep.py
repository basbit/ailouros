from __future__ import annotations

from backend.App.spec.domain.document_validator import _detect_cycle


def test_detect_cycle_deep_acyclic_chain_no_recursion_error():
    chain_length = 5000
    edges = {f"n{i}": [f"n{i + 1}"] for i in range(chain_length)}
    edges[f"n{chain_length}"] = []
    assert _detect_cycle(edges) == ()


def test_detect_cycle_deep_chain_with_cycle_at_end():
    chain_length = 5000
    edges = {f"n{i}": [f"n{i + 1}"] for i in range(chain_length)}
    edges[f"n{chain_length}"] = ["n0"]
    cycle = _detect_cycle(edges)
    assert cycle != ()
    assert len(cycle) == chain_length + 1


def test_detect_cycle_self_loop():
    assert _detect_cycle({"a": ["a"]}) == ("a",)


def test_detect_cycle_two_node_loop():
    cycle = _detect_cycle({"a": ["b"], "b": ["a"]})
    assert set(cycle) == {"a", "b"}


def test_detect_cycle_disconnected_components():
    edges = {
        "a": ["b"],
        "b": [],
        "c": ["d"],
        "d": ["c"],
    }
    cycle = _detect_cycle(edges)
    assert set(cycle) == {"c", "d"}

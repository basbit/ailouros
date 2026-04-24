"""Tests for NodeDef, EdgeDef, GraphDefinition, and GraphRuntimePort."""
from __future__ import annotations

import dataclasses

import pytest

from backend.App.orchestration.domain.graph_runtime import (
    EdgeDef,
    GraphDefinition,
    GraphRuntimePort,
    NodeDef,
)


# ---------------------------------------------------------------------------
# 1. NodeDef is a frozen dataclass
# ---------------------------------------------------------------------------
def test_node_def_frozen():
    node = NodeDef(name="start", fn=lambda s: s)
    with pytest.raises(dataclasses.FrozenInstanceError):
        node.name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. EdgeDef is a frozen dataclass
# ---------------------------------------------------------------------------
def test_edge_def_frozen():
    edge = EdgeDef(from_node="a", to_node="b")
    with pytest.raises(dataclasses.FrozenInstanceError):
        edge.from_node = "c"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 3. GraphDefinition has correct defaults
# ---------------------------------------------------------------------------
def test_graph_definition_defaults():
    graph = GraphDefinition()
    assert graph.nodes == []
    assert graph.edges == []
    assert graph.entry_point == ""


# ---------------------------------------------------------------------------
# 4. GraphRuntimePort cannot be instantiated directly
# ---------------------------------------------------------------------------
def test_graph_runtime_port_is_abstract():
    with pytest.raises(TypeError):
        GraphRuntimePort()  # type: ignore[abstract]

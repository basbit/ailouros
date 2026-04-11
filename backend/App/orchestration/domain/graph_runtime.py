"""Graph runtime port for pipeline execution (L-1).

Rules (INV-7): domain layer — stdlib + typing only.
No langgraph, fastapi, redis, httpx imports here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NodeDef:
    """Definition of a single graph node."""
    name: str
    fn: Any  # Callable[[state], dict] — typed as Any to avoid runtime import


@dataclass(frozen=True)
class EdgeDef:
    """Definition of a direct edge between two nodes."""
    from_node: str
    to_node: str


@dataclass(frozen=True)
class ConditionalEdgeDef:
    """Definition of a conditional edge that uses a router function.

    router: Callable[[state], str] — returns a route key.
    route_map: {route_key: to_node} — maps keys to target nodes.
    """
    from_node: str
    router: Any  # Callable[[state], str]
    route_map: dict  # dict[str, str]


@dataclass
class GraphDefinition:
    """Data description of a pipeline graph — framework-agnostic.

    Passed to GraphRuntimePort.compile() to produce a runnable graph.
    """
    nodes: list[NodeDef] = field(default_factory=list)
    edges: list[EdgeDef] = field(default_factory=list)
    conditional_edges: list[ConditionalEdgeDef] = field(default_factory=list)
    entry_point: str = ""


class GraphRuntimePort(ABC):
    """Abstraction over graph execution framework (e.g. LangGraph).

    Application layer builds GraphDefinition; infrastructure compiles it.
    INV-7: this module must remain framework-free.
    """

    @abstractmethod
    def compile(self, definition: GraphDefinition, state_schema: Any) -> Any:
        """Compile a GraphDefinition into a runnable graph object.

        Args:
            definition: Framework-agnostic graph structure.
            state_schema: State TypedDict class used by the graph.

        Returns:
            Compiled graph object (framework-specific, opaque to callers).
        """

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NodeDef:
    name: str
    fn: Any  # Callable[[state], dict] — typed as Any to avoid runtime import


@dataclass(frozen=True)
class EdgeDef:
    from_node: str
    to_node: str


@dataclass(frozen=True)
class ConditionalEdgeDef:
    from_node: str
    router: Any  # Callable[[state], str]
    route_map: dict  # dict[str, str]


@dataclass
class GraphDefinition:
    nodes: list[NodeDef] = field(default_factory=list)
    edges: list[EdgeDef] = field(default_factory=list)
    conditional_edges: list[ConditionalEdgeDef] = field(default_factory=list)
    entry_point: str = ""


class GraphRuntimePort(ABC):

    @abstractmethod
    def compile(self, definition: GraphDefinition, state_schema: Any) -> Any:
        ...

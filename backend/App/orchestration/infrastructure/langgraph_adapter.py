from __future__ import annotations

from typing import Any

from backend.App.orchestration.domain.graph_runtime import GraphDefinition, GraphRuntimePort


class LangGraphAdapter(GraphRuntimePort):

    def compile(self, definition: GraphDefinition, state_schema: Any) -> Any:
        from langgraph.graph import END, START, StateGraph  # langgraph isolated here

        graph = StateGraph(state_schema)

        for node in definition.nodes:
            graph.add_node(node.name, node.fn)

        for edge in definition.edges:
            from_node = START if edge.from_node == "__start__" else edge.from_node
            to_node = END if edge.to_node == "__end__" else edge.to_node
            graph.add_edge(from_node, to_node)

        for cond_edge in (definition.conditional_edges or []):
            from_node = START if cond_edge.from_node == "__start__" else cond_edge.from_node
            route_map = {
                k: (END if v == "__end__" else v)
                for k, v in cond_edge.route_map.items()
            }
            graph.add_conditional_edges(from_node, cond_edge.router, route_map)

        if definition.entry_point:
            graph.set_entry_point(definition.entry_point)

        return graph.compile()


def _lazy_langgraph_sentinel(name: str) -> str:
    from langgraph.graph import END, START
    return {"END": END, "START": START}[name]


def get_END() -> str:
    return _lazy_langgraph_sentinel("END")


def get_START() -> str:
    return _lazy_langgraph_sentinel("START")

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass

_BUILTIN_NAMES: frozenset[str] = frozenset(dir(builtins))


@dataclass(frozen=True)
class InvariantPredicate:
    name: str
    expression: str
    bindings: tuple[str, ...]


class InvariantPredicateError(ValueError):
    pass


class _FreeNameCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self._names: list[str] = []
        self._assigned: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self._assigned.add(node.id)
        elif isinstance(node.ctx, ast.Load):
            self._names.append(node.id)

    def free_names(self) -> tuple[str, ...]:
        seen: set[str] = set()
        result: list[str] = []
        for name in self._names:
            if (
                name not in _BUILTIN_NAMES
                and name not in self._assigned
                and name not in seen
            ):
                seen.add(name)
                result.append(name)
        return tuple(result)


def parse_predicate(name: str, expression: str) -> InvariantPredicate:
    if not expression.strip():
        raise InvariantPredicateError(
            f"invariant {name!r}: predicate expression is empty"
        )
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise InvariantPredicateError(
            f"invariant {name!r}: predicate {expression!r} is not valid Python "
            f"expression syntax: {exc}"
        ) from exc
    collector = _FreeNameCollector()
    collector.visit(tree)
    return InvariantPredicate(
        name=name,
        expression=expression,
        bindings=collector.free_names(),
    )


__all__ = ["InvariantPredicate", "InvariantPredicateError", "parse_predicate"]

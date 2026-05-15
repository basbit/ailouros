from __future__ import annotations

import ast
from dataclasses import dataclass


class NoHolesFoundError(ValueError):
    pass


@dataclass(frozen=True)
class Hole:
    function_name: str
    qualname: str
    signature: str
    body_lineno_start: int
    body_lineno_end: int


def _is_hole_body(body: list[ast.stmt]) -> bool:
    if len(body) != 1:
        return False
    stmt = body[0]
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return stmt.value.value is ...
    return False


def _sig_from_funcdef(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    unparsed = ast.unparse(node)
    idx = unparsed.find(":\n")
    if idx == -1:
        idx = unparsed.find(":")
    return unparsed[: idx + 1]


def _collect_holes(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    prefix: str,
) -> list[Hole]:
    qualname = f"{prefix}.{node.name}" if prefix else node.name
    holes: list[Hole] = []

    if _is_hole_body(node.body):
        try:
            sig = _sig_from_funcdef(node)
        except Exception as exc:
            raise ValueError(
                f"Cannot extract signature for {qualname!r}: {exc}"
            ) from exc
        body_start = node.body[0].lineno
        body_end = node.body[-1].end_lineno or node.body[-1].lineno
        holes.append(
            Hole(
                function_name=node.name,
                qualname=qualname,
                signature=sig,
                body_lineno_start=body_start,
                body_lineno_end=body_end,
            )
        )
    else:
        for child in ast.walk(node):
            if child is node:
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                holes.extend(_collect_holes(child, qualname))

    return holes


def extract_holes(source: str) -> tuple[Hole, ...]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"Cannot parse source: {exc}") from exc

    holes: list[Hole] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            holes.extend(_collect_holes(node, ""))
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    holes.extend(_collect_holes(child, node.name))

    if not holes:
        raise NoHolesFoundError(
            "No holes found in source. "
            "Mark function bodies with '...' or 'pass' to indicate holes."
        )
    return tuple(holes)


def _public_surface(source: str) -> frozenset[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return frozenset()

    entries: list[str] = []

    def _func_sig(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        try:
            unparsed = ast.unparse(node)
            first_line = unparsed.split("\n")[0]
            return first_line
        except Exception:
            return node.name

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                entries.append(_func_sig(node))
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                entries.append(f"class {node.name}")
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if not child.name.startswith("_"):
                            entries.append(f"{node.name}.{_func_sig(child)}")

    return frozenset(entries)


def compare_public_surface(before: str, after: str) -> tuple[str, ...]:
    before_surface = _public_surface(before)
    after_surface = _public_surface(after)
    differences: list[str] = []
    for entry in before_surface - after_surface:
        differences.append(f"removed: {entry}")
    for entry in after_surface - before_surface:
        differences.append(f"added: {entry}")
    return tuple(sorted(differences))


__all__ = [
    "Hole",
    "NoHolesFoundError",
    "compare_public_surface",
    "extract_holes",
]

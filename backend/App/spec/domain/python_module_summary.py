from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass(frozen=True)
class FunctionSummary:
    name: str
    is_async: bool
    signature: str


@dataclass(frozen=True)
class ClassSummary:
    name: str
    bases: tuple[str, ...]
    methods: tuple[FunctionSummary, ...]


@dataclass(frozen=True)
class ModuleSummary:
    module_path: str
    docstring: str
    functions: tuple[FunctionSummary, ...] = field(default_factory=tuple)
    classes: tuple[ClassSummary, ...] = field(default_factory=tuple)


def _name_is_public(name: str) -> bool:
    return not name.startswith("_")


def _unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    return ast.unparse(node)


def _describe_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> FunctionSummary:
    parameters: list[str] = []
    for argument in node.args.args:
        annotation = _unparse(argument.annotation)
        parameters.append(
            f"{argument.arg}: {annotation}" if annotation else argument.arg
        )
    if node.args.vararg is not None:
        parameters.append(f"*{node.args.vararg.arg}")
    for argument in node.args.kwonlyargs:
        annotation = _unparse(argument.annotation)
        parameters.append(
            f"{argument.arg}: {annotation}" if annotation else argument.arg
        )
    if node.args.kwarg is not None:
        parameters.append(f"**{node.args.kwarg.arg}")
    returns = _unparse(node.returns)
    return_clause = f" -> {returns}" if returns else ""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    signature = f"{prefix} {node.name}({', '.join(parameters)}){return_clause}: ..."
    return FunctionSummary(
        name=node.name,
        is_async=isinstance(node, ast.AsyncFunctionDef),
        signature=signature,
    )


def _describe_class(node: ast.ClassDef) -> ClassSummary:
    methods: list[FunctionSummary] = []
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _name_is_public(child.name) or child.name == "__init__":
                methods.append(_describe_function(child))
    bases = tuple(_unparse(base) for base in node.bases if _unparse(base))
    return ClassSummary(
        name=node.name,
        bases=bases,
        methods=tuple(methods),
    )


def summarise_python_module(source: str, module_path: str) -> ModuleSummary:
    tree = ast.parse(source)
    docstring = ast.get_docstring(tree) or ""
    functions: list[FunctionSummary] = []
    classes: list[ClassSummary] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _name_is_public(node.name):
                functions.append(_describe_function(node))
        elif isinstance(node, ast.ClassDef):
            if _name_is_public(node.name):
                classes.append(_describe_class(node))
    return ModuleSummary(
        module_path=module_path,
        docstring=docstring,
        functions=tuple(functions),
        classes=tuple(classes),
    )


__all__ = [
    "ClassSummary",
    "FunctionSummary",
    "ModuleSummary",
    "summarise_python_module",
]

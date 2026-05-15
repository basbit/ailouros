from __future__ import annotations

import ast

from backend.App.spec.domain.dsl_block import FencedDslBlock
from backend.App.spec.domain.dsl_registry import (
    DslFinding,
    DslParseResult,
)


class PythonSignatureParser:
    kind = "python-sig"

    def parse(self, block: FencedDslBlock) -> DslParseResult:
        findings: list[DslFinding] = []
        normalised = self._stub_unfinished_signatures(block.content)
        try:
            module = ast.parse(normalised)
        except SyntaxError as exception:
            findings.append(
                DslFinding(
                    kind=self.kind,
                    severity="error",
                    message=f"Python signature is not valid: {exception.msg}",
                    line_start=block.line_start + (exception.lineno or 1) - 1,
                )
            )
            return DslParseResult(kind=self.kind, payload={}, findings=tuple(findings))

        functions: list[dict[str, object]] = []
        classes: list[dict[str, object]] = []
        for node in module.body:
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                functions.append(self._describe_function(node))
            elif isinstance(node, ast.ClassDef):
                classes.append(self._describe_class(node))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            else:
                findings.append(
                    DslFinding(
                        kind=self.kind,
                        severity="warning",
                        message=(
                            "python-sig block should only contain function or "
                            "class signatures; top-level statements are ignored."
                        ),
                        line_start=block.line_start + (node.lineno or 1) - 1,
                    )
                )

        if not functions and not classes:
            findings.append(
                DslFinding(
                    kind=self.kind,
                    severity="error",
                    message="python-sig block has no function or class signatures.",
                    line_start=block.line_start,
                )
            )

        return DslParseResult(
            kind=self.kind,
            payload={"functions": functions, "classes": classes},
            findings=tuple(findings),
        )

    def _stub_unfinished_signatures(self, content: str) -> str:
        rewritten_lines: list[str] = []
        for raw_line in content.splitlines():
            stripped = raw_line.rstrip()
            if stripped.endswith(":"):
                indent = raw_line[: len(raw_line) - len(raw_line.lstrip())]
                rewritten_lines.append(raw_line)
                rewritten_lines.append(indent + "    ...")
            else:
                rewritten_lines.append(raw_line)
        return "\n".join(rewritten_lines)

    def _describe_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> dict[str, object]:
        return {
            "name": node.name,
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "params": self._describe_arguments(node.args),
            "returns": self._unparse(node.returns),
        }

    def _describe_class(self, node: ast.ClassDef) -> dict[str, object]:
        members: list[dict[str, object]] = []
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                members.append(self._describe_function(child))
        return {
            "name": node.name,
            "bases": [self._unparse(base) for base in node.bases],
            "methods": members,
        }

    def _describe_arguments(self, args: ast.arguments) -> list[dict[str, object]]:
        described: list[dict[str, object]] = []
        for argument in args.args:
            described.append(
                {
                    "name": argument.arg,
                    "annotation": self._unparse(argument.annotation),
                }
            )
        if args.vararg is not None:
            described.append(
                {"name": "*" + args.vararg.arg, "annotation": self._unparse(args.vararg.annotation)}
            )
        for argument in args.kwonlyargs:
            described.append(
                {"name": argument.arg, "annotation": self._unparse(argument.annotation)}
            )
        if args.kwarg is not None:
            described.append(
                {"name": "**" + args.kwarg.arg, "annotation": self._unparse(args.kwarg.annotation)}
            )
        return described

    def _unparse(self, node: ast.AST | None) -> str:
        if node is None:
            return ""
        return ast.unparse(node)


__all__ = ["PythonSignatureParser"]

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Optional


_OWNED_TAG = "config-discipline: code-owned"
_SCAN_LITERAL_TYPES = (ast.List, ast.Dict, ast.Set, ast.Tuple)
_MIN_LITERAL_LEN = 3
_BACKEND_ROOT = Path("backend")


def _iter_python_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            yield root
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path


def _line_comment(source_lines: list[str], lineno: int) -> str:
    if lineno <= 0 or lineno > len(source_lines):
        return ""
    line = source_lines[lineno - 1]
    if "#" not in line:
        return ""
    return line.split("#", 1)[1].strip()


def _is_owned(comment: str) -> bool:
    return _OWNED_TAG in comment


def _literal_length(node: ast.AST) -> int:
    if isinstance(node, ast.List):
        return len(node.elts)
    if isinstance(node, ast.Tuple):
        return len(node.elts)
    if isinstance(node, ast.Set):
        return len(node.elts)
    if isinstance(node, ast.Dict):
        return len(node.keys)
    return 0


def _is_uppercase_name(name: str) -> bool:
    return bool(re.fullmatch(r"_*[A-Z][A-Z0-9_]+", name))


def _scan_file(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines()
    findings: list[dict[str, object]] = []
    for node in tree.body:
        targets: list[str] = []
        value: Optional[ast.AST] = None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and _is_uppercase_name(target.id):
                    targets.append(target.id)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if _is_uppercase_name(node.target.id):
                targets.append(node.target.id)
            value = node.value
        if not targets or value is None:
            continue
        if not isinstance(value, _SCAN_LITERAL_TYPES):
            continue
        if _literal_length(value) < _MIN_LITERAL_LEN:
            continue
        comment = _line_comment(lines, node.lineno)
        if _is_owned(comment):
            continue
        for target_name in targets:
            findings.append(
                {
                    "path": str(path),
                    "lineno": node.lineno,
                    "name": target_name,
                    "literal_kind": type(value).__name__,
                    "literal_size": _literal_length(value),
                    "hint": (
                        f"Move {target_name!r} to app/config/ and load via "
                        f"backend.App.shared.infrastructure.app_config_load, "
                        f"or tag the line with `# {_OWNED_TAG}` if it must stay in code."
                    ),
                }
            )
    return findings


def _format_text(findings: list[dict[str, object]]) -> str:
    if not findings:
        return "no findings"
    lines = [f"hardcoded literal audit — {len(findings)} finding(s):"]
    for entry in findings:
        lines.append(
            f"  {entry['path']}:{entry['lineno']}: {entry['name']} "
            f"({entry['literal_kind']} size={entry['literal_size']})"
        )
        lines.append(f"    -> {entry['hint']}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit backend for hardcoded top-level literals.")
    parser.add_argument(
        "paths",
        nargs="*",
        default=[str(_BACKEND_ROOT)],
        help="Files or directories to scan (defaults to backend/).",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format.",
    )
    parser.add_argument(
        "--max-findings",
        type=int,
        default=0,
        help="Exit with non-zero when findings exceed this number (default 0 = any finding fails).",
    )
    args = parser.parse_args(argv)

    roots = [Path(p) for p in args.paths]
    findings: list[dict[str, object]] = []
    for path in _iter_python_files(roots):
        try:
            findings.extend(_scan_file(path))
        except SyntaxError as exc:
            findings.append(
                {
                    "path": str(path),
                    "lineno": getattr(exc, "lineno", 0) or 0,
                    "name": "<syntax-error>",
                    "literal_kind": "",
                    "literal_size": 0,
                    "hint": str(exc),
                }
            )

    if args.format == "json":
        print(json.dumps({"findings": findings, "count": len(findings)}, indent=2))
    else:
        print(_format_text(findings))
    return 0 if len(findings) <= args.max_findings else 1


if __name__ == "__main__":
    sys.exit(main())

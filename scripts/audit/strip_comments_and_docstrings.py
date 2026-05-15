from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path


_ALLOWED_INLINE = (
    "# noqa",
    "# type: ignore",
    "# config-discipline",
)


def _is_allowed_comment_line(stripped: str) -> bool:
    if stripped.startswith("#!"):
        return True
    for marker in _ALLOWED_INLINE:
        if marker in stripped:
            return True
    return False


def _strip_inline_comment(line: str) -> str:
    in_single = False
    in_double = False
    in_triple_single = False
    in_triple_double = False
    index = 0
    while index < len(line):
        ch = line[index]
        if not (in_single or in_double or in_triple_single or in_triple_double):
            if line[index: index + 3] == "'''":
                in_triple_single = True
                index += 3
                continue
            if line[index: index + 3] == '"""':
                in_triple_double = True
                index += 3
                continue
            if ch == "'":
                in_single = True
                index += 1
                continue
            if ch == '"':
                in_double = True
                index += 1
                continue
            if ch == "#":
                tail = line[index:]
                if any(marker in tail for marker in _ALLOWED_INLINE):
                    return line
                return line[:index].rstrip() + "\n"
        else:
            if in_triple_single and line[index: index + 3] == "'''":
                in_triple_single = False
                index += 3
                continue
            if in_triple_double and line[index: index + 3] == '"""':
                in_triple_double = False
                index += 3
                continue
            if in_single and ch == "'" and line[index - 1] != "\\":
                in_single = False
            elif in_double and ch == '"' and line[index - 1] != "\\":
                in_double = False
        index += 1
    return line


def _drop_docstring_nodes(source: str, path: Path) -> str:
    tree = ast.parse(source, filename=str(path))
    spans: list[tuple[int, int, bool, int]] = []

    def _capture(body: list[ast.stmt], owner_only_docstring: bool, indent: int) -> None:
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            start = first.lineno
            end = (first.end_lineno or first.lineno) + 1
            spans.append((start, end, owner_only_docstring, indent))

    _capture(tree.body, owner_only_docstring=False, indent=0)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            only_docstring = len(node.body) == 1 and (
                isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            )
            indent = node.col_offset + 4
            _capture(node.body, only_docstring, indent)

    if not spans:
        return source
    lines = source.splitlines(keepends=True)
    spans.sort(key=lambda item: item[0], reverse=True)
    for start, end, only_docstring, indent in spans:
        del lines[start - 1: end - 1]
        if only_docstring:
            lines.insert(start - 1, " " * indent + "pass\n")
    return "".join(lines)


def _drop_full_line_comments(text: str) -> str:
    out_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("#") and not _is_allowed_comment_line(stripped):
            continue
        out_lines.append(line)
    return "".join(out_lines)


def _drop_inline_comments(text: str) -> str:
    return "".join(_strip_inline_comment(line) for line in text.splitlines(keepends=True))


def clean_file(path: Path) -> tuple[bool, str]:
    original = path.read_text(encoding="utf-8")
    after_docstrings = _drop_docstring_nodes(original, path)
    after_full = _drop_full_line_comments(after_docstrings)
    after_inline = _drop_inline_comments(after_full)
    cleaned = re.sub(r"\n{3,}", "\n\n", after_inline)
    if cleaned != original:
        try:
            ast.parse(cleaned, filename=str(path))
        except SyntaxError as exc:
            return False, f"syntax broken: {exc}"
        return True, cleaned
    return False, "no change"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strip docstrings and comments from Python files.")
    parser.add_argument("paths", nargs="+", help="Files or directories to clean.")
    parser.add_argument("--dry-run", action="store_true", help="Only report what would change.")
    args = parser.parse_args(argv)
    targets: list[Path] = []
    for raw in args.paths:
        path = Path(raw)
        if path.is_file():
            targets.append(path)
        elif path.is_dir():
            targets.extend(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)
    changed_count = 0
    for path in targets:
        ok, payload = clean_file(path)
        if not ok:
            if payload != "no change":
                print(f"SKIP {path}: {payload}")
            continue
        if args.dry_run:
            print(f"WOULD CLEAN {path}")
        else:
            path.write_text(payload, encoding="utf-8")
            print(f"cleaned {path}")
        changed_count += 1
    print(f"{changed_count} file(s) {'would change' if args.dry_run else 'changed'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

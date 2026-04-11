"""Read-only local evidence tools for workspace-backed agent investigations."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend.App.workspace.infrastructure.code_analysis.scan import analyze_workspace


def evidence_tools_available(workspace_root: str) -> bool:
    root = (workspace_root or "").strip()
    return bool(root) and Path(root).expanduser().is_dir()


def evidence_tools_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "grep_context",
                "description": (
                    "Search workspace files for a literal query and return surrounding line context. "
                    "Use for evidence-backed claims about repository contents."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Literal text to search for"},
                        "globs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional file globs such as ['*.py', 'src/**/*.ts']",
                        },
                        "max_hits": {
                            "type": "integer",
                            "description": "Maximum number of matches to return",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "find_class_definition",
                "description": (
                    "Find exact symbol definitions from code-analysis entities and return file path, "
                    "line, entity kind, and excerpt."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Exact symbol name to locate"},
                    },
                    "required": ["symbol"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "find_symbol_usages",
                "description": (
                    "Find exact word-boundary usages of a symbol in workspace files and return file path, "
                    "line, and excerpt."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Exact symbol name to search"},
                        "max_hits": {
                            "type": "integer",
                            "description": "Maximum number of matches to return",
                            "default": 10,
                        },
                    },
                    "required": ["symbol"],
                },
            },
        },
    ]


def _safe_root(workspace_root: str) -> Path:
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"workspace_root is not a directory: {workspace_root}")
    return root


def _read_excerpt(path: Path, line_no: int, *, context_before: int = 1, context_after: int = 2) -> tuple[int, int, str]:
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start_line = max(1, line_no - context_before)
    end_line = min(len(content), line_no + context_after)
    excerpt = "\n".join(content[start_line - 1:end_line]).strip()
    return start_line, end_line, excerpt


def _glob_match(rel_path: str, globs: list[str]) -> bool:
    if not globs:
        return True
    rel = Path(rel_path)
    return any(rel.match(glob) for glob in globs)


def grep_context(
    workspace_root: str,
    *,
    query: str,
    globs: list[str] | None = None,
    max_hits: int = 5,
) -> str:
    root = _safe_root(workspace_root)
    needle = (query or "").strip()
    if not needle:
        return json.dumps({"hits": [], "error": "query is required"}, ensure_ascii=False)
    hit_limit = max(1, min(int(max_hits or 5), 50))
    patterns = [str(g or "").strip() for g in (globs or []) if str(g or "").strip()]
    hits: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(root).as_posix()
        if not _glob_match(rel_path, patterns):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for idx, line in enumerate(lines, start=1):
            if needle not in line:
                continue
            start_line = max(1, idx - 1)
            end_line = min(len(lines), idx + 2)
            excerpt = "\n".join(lines[start_line - 1:end_line]).strip()
            hits.append(
                {
                    "path": rel_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "excerpt": excerpt,
                }
            )
            if len(hits) >= hit_limit:
                return json.dumps({"hits": hits}, ensure_ascii=False, indent=2)
    return json.dumps({"hits": hits}, ensure_ascii=False, indent=2)


def find_class_definition(workspace_root: str, *, symbol: str) -> str:
    root = _safe_root(workspace_root)
    target = (symbol or "").strip()
    if not target:
        return json.dumps({"hits": [], "error": "symbol is required"}, ensure_ascii=False)
    analysis = analyze_workspace(root)
    hits: list[dict[str, Any]] = []
    for file_item in list(analysis.get("files") or []):
        rel_path = str(file_item.get("path") or "").strip()
        if not rel_path:
            continue
        for entity in list(file_item.get("entities") or []):
            if str(entity.get("name") or "").strip() != target:
                continue
            line_no = int(entity.get("line") or 0)
            if line_no <= 0:
                continue
            abs_path = root / rel_path
            try:
                start_line, end_line, excerpt = _read_excerpt(abs_path, line_no)
            except OSError:
                continue
            hits.append(
                {
                    "path": rel_path,
                    "kind": str(entity.get("kind") or ""),
                    "line": line_no,
                    "start_line": start_line,
                    "end_line": end_line,
                    "excerpt": excerpt,
                }
            )
    return json.dumps({"hits": hits}, ensure_ascii=False, indent=2)


def find_symbol_usages(workspace_root: str, *, symbol: str, max_hits: int = 10) -> str:
    root = _safe_root(workspace_root)
    target = (symbol or "").strip()
    if not target:
        return json.dumps({"hits": [], "error": "symbol is required"}, ensure_ascii=False)
    pattern = re.compile(rf"\b{re.escape(target)}\b")
    hit_limit = max(1, min(int(max_hits or 10), 100))
    hits: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for idx, line in enumerate(lines, start=1):
            if not pattern.search(line):
                continue
            start_line = max(1, idx - 1)
            end_line = min(len(lines), idx + 2)
            excerpt = "\n".join(lines[start_line - 1:end_line]).strip()
            hits.append(
                {
                    "path": rel_path,
                    "line": idx,
                    "start_line": start_line,
                    "end_line": end_line,
                    "excerpt": excerpt,
                }
            )
            if len(hits) >= hit_limit:
                return json.dumps({"hits": hits}, ensure_ascii=False, indent=2)
    return json.dumps({"hits": hits}, ensure_ascii=False, indent=2)

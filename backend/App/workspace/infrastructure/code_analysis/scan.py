"""Build code_analysis.json: file tree + entities by language.

Uses a pluggable analyzer registry. Built-in analyzers: python, javascript,
typescript, go, php. Extend via SWARM_CODE_ANALYZER_PLUGINS env var
(comma-separated dotted module paths with a ``register_analyzers(registry)`` function).
"""

from __future__ import annotations

import ast
import importlib
import json
import logging
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.App.workspace.infrastructure.code_analysis.relations import build_architecture_map
from backend.App.workspace.infrastructure.code_analysis.tree_sitter_extract import (
    extract_with_tree_sitter,
)

_logger = logging.getLogger(__name__)

_DEFAULT_IGNORE_DIRS = (
    ".git,.svn,.hg,.venv,venv,__pycache__,.pytest_cache,.mypy_cache,"
    "node_modules,.idea,.vscode,dist,build,.tox,artifacts"
)
_IGNORE_DIR_NAMES = frozenset(
    d.strip() for d in os.getenv("SWARM_CODE_ANALYSIS_IGNORE_DIRS", _DEFAULT_IGNORE_DIRS).split(",") if d.strip()
)

_EXT_LANG: dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".php": "php",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin",
    ".rs": "rust",
    ".vue": "vue",
    ".svelte": "svelte",
}
# Extend via env: SWARM_EXT_LANG_EXTRA=".cs:csharp,.swift:swift"
_extra_ext = os.getenv("SWARM_EXT_LANG_EXTRA", "").strip()
if _extra_ext:
    for pair in _extra_ext.split(","):
        if ":" in pair:
            ext, lang = pair.split(":", 1)
            _EXT_LANG[ext.strip()] = lang.strip()

_MAX_FILES = int(os.getenv("SWARM_CODE_ANALYSIS_MAX_FILES", "500"))
_MAX_FILE_BYTES = int(os.getenv("SWARM_CODE_ANALYSIS_MAX_FILE_BYTES", "256000"))


# ---------------------------------------------------------------------------
# Pluggable analyzer registry (P3-2)
# ---------------------------------------------------------------------------

# Analyzer signature: (source: str, path: str) → list[dict]
AnalyzerFn = Callable[[str, str], list[dict[str, Any]]]


class AnalyzerRegistry:
    """Registry of per-language entity extractors.

    Built-in analyzers are registered at module load time. Additional analyzers
    can be registered via :meth:`register` or loaded from plugin modules listed
    in ``SWARM_CODE_ANALYZER_PLUGINS`` (comma-separated dotted paths, each must
    expose ``register_analyzers(registry: AnalyzerRegistry)``).
    """

    def __init__(self) -> None:
        self._analyzers: dict[str, AnalyzerFn] = {}

    def register(self, language: str, fn: AnalyzerFn) -> None:
        self._analyzers[language] = fn

    def get(self, language: str) -> AnalyzerFn | None:
        return self._analyzers.get(language)

    def supported_languages(self) -> list[str]:
        return sorted(self._analyzers.keys())


_analyzer_registry = AnalyzerRegistry()


def get_analyzer_registry() -> AnalyzerRegistry:
    return _analyzer_registry


def _rel_tree(root: Path, rel: Path) -> dict[str, Any]:
    """File tree node: file or directory."""
    p = root / rel
    if p.is_file():
        return {"type": "file", "name": p.name}
    children: list[dict[str, Any]] = []
    try:
        for name in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if name.name in _IGNORE_DIR_NAMES:
                continue
            cr = rel / name.name
            children.append(_rel_tree(root, cr))
    except OSError:
        pass
    return {"type": "dir", "name": p.name, "children": children}


def _entities_python(source: str, path: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [{"kind": "parse_error", "name": path, "line": 1}]
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            out.append({"kind": "class", "name": node.name, "line": node.lineno})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append({"kind": "function", "name": node.name, "line": node.lineno})
    return out


_RE_EXPORT_FN = re.compile(
    r"export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)",
    re.MULTILINE,
)
_RE_CONST_FN = re.compile(
    r"export\s+const\s+(\w+)\s*=\s*(?:async\s*)?\(",
    re.MULTILINE,
)
_RE_CLASS = re.compile(
    r"export\s+(?:default\s+)?class\s+(\w+)",
    re.MULTILINE,
)
_RE_ROUTE = re.compile(
    r"(?:app|router)\.(?:get|post|put|patch|delete|route)\s*\(\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE | re.MULTILINE,
)
_RE_FLASK = re.compile(
    r"@\w+\.route\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)
_RE_FASTAPI = re.compile(
    r"@\w+\.(?:get|post|put|patch|delete)\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)


def _entities_js_like(source: str, path: str, lang: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in _RE_EXPORT_FN.finditer(source):
        out.append({"kind": "function", "name": m.group(1), "line": source[: m.start()].count("\n") + 1})
    for m in _RE_CONST_FN.finditer(source):
        out.append({"kind": "function", "name": m.group(1), "line": source[: m.start()].count("\n") + 1})
    for m in _RE_CLASS.finditer(source):
        out.append({"kind": "class", "name": m.group(1), "line": source[: m.start()].count("\n") + 1})
    for m in _RE_ROUTE.finditer(source):
        out.append({"kind": "route", "path": m.group(1), "line": source[: m.start()].count("\n") + 1})
    for m in _RE_FLASK.finditer(source):
        out.append({"kind": "route", "path": m.group(1), "line": source[: m.start()].count("\n") + 1})
    for m in _RE_FASTAPI.finditer(source):
        out.append({"kind": "route", "path": m.group(1), "line": source[: m.start()].count("\n") + 1})
    # React component heuristic: export default Name
    dm = re.search(r"export\s+default\s+(?:function\s+)?(\w+)", source)
    if dm:
        out.append(
            {
                "kind": "component",
                "name": dm.group(1),
                "line": source[: dm.start()].count("\n") + 1,
            }
        )
    return out


_RE_GO_FUNC = re.compile(r"^func\s+(?:\([^)]+\)\s*)?(\w+)\s*\(", re.MULTILINE)
_RE_GO_TYPE = re.compile(r"^type\s+(\w+)\s+", re.MULTILINE)


def _entities_go(source: str, path: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in _RE_GO_FUNC.finditer(source):
        out.append({"kind": "function", "name": m.group(1), "line": source[: m.start()].count("\n") + 1})
    for m in _RE_GO_TYPE.finditer(source):
        out.append({"kind": "type", "name": m.group(1), "line": source[: m.start()].count("\n") + 1})
    return out


_RE_PHP_CLASS = re.compile(r"class\s+(\w+)", re.MULTILINE)
_RE_PHP_FUNC = re.compile(r"function\s+(\w+)\s*\(", re.MULTILINE)


def _entities_php(source: str, path: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in _RE_PHP_CLASS.finditer(source):
        out.append({"kind": "class", "name": m.group(1), "line": source[: m.start()].count("\n") + 1})
    for m in _RE_PHP_FUNC.finditer(source):
        out.append({"kind": "function", "name": m.group(1), "line": source[: m.start()].count("\n") + 1})
    return out


def _extract_file(
    path: Path,
    rel: str,
    lang: str,
    *,
    tree_sitter_disabled: bool = False,
) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if b"\x00" in raw[:4000]:
            return {"path": rel, "language": lang, "skipped": "binary"}
        if len(raw) > _MAX_FILE_BYTES:
            return {"path": rel, "language": lang, "skipped": "too_large"}
        text = raw.decode("utf-8", errors="replace")
    except OSError as e:
        return {"path": rel, "language": lang, "error": str(e)}

    ts = extract_with_tree_sitter(text, rel, lang, disabled=tree_sitter_disabled)
    if ts:
        return {
            "path": rel,
            "language": lang,
            "entities": ts["entities"],
            "tree_sitter": {
                "enabled": True,
                "grammar": ts.get("grammar", ""),
            },
        }

    # P3-2: Use pluggable analyzer registry instead of if/elif chain
    analyzer = _analyzer_registry.get(lang)
    if analyzer:
        entities = analyzer(text, rel)
    else:
        entities = [{"kind": "file", "name": Path(rel).name, "line": 1}]

    return {"path": rel, "language": lang, "entities": entities}


def _extract_signature(source: str, entity: dict[str, Any], max_lines: int = 12) -> str:
    """Extract a code signature snippet starting at the entity's line."""
    line_no = entity.get("line", 0)
    if line_no <= 0:
        return ""
    lines = source.splitlines()
    start = line_no - 1
    if start >= len(lines):
        return ""
    end = min(start + max_lines, len(lines))
    return "\n".join(lines[start:end])


def _extract_project_conventions(
    root: Path,
    files_out: list[dict[str, Any]],
    by_lang: dict[str, int],
) -> dict[str, Any]:
    """Extract real code examples from the most representative project files.

    No hardcoded framework/language detection — the LLM model determines
    conventions from the actual code snippets provided here.
    """
    # Pick top files by entity count — these are the most representative
    files_by_entity_count = sorted(
        [f for f in files_out if len(f.get("entities") or []) >= 2],
        key=lambda f: len(f.get("entities") or []),
        reverse=True,
    )
    signatures: list[str] = []
    for f in files_by_entity_count[:8]:
        fpath = root / f["path"]
        if not fpath.is_file():
            continue
        try:
            src = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for e in (f.get("entities") or []):
            if e.get("kind") in ("class", "type") and len(signatures) < 5:
                sig = _extract_signature(src, e, max_lines=12)
                if sig and len(sig) > 20:
                    signatures.append(f"// {f['path']}\n{sig}")
        if len(signatures) >= 5:
            break

    return {
        "example_signatures": signatures,
    }


def analyze_workspace(
    root: Path,
    *,
    languages_filter: Optional[list[str]] = None,
    tree_sitter_disabled: bool = False,
) -> dict[str, Any]:
    """Полный payload для code_analysis.json."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        return {
            "error": "not_a_directory",
            "root": str(root),
            "file_tree": {},
            "files": [],
            "relation_graph": {"schema": "swarm_relation_graph/v1", "edges": [], "nodes": []},
            "stats": {},
        }

    lf = {x.strip().lower() for x in languages_filter} if languages_filter else None
    file_tree = _rel_tree(root, Path("."))
    files_out: list[dict[str, Any]] = []
    by_lang: dict[str, int] = {}
    n = 0

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in sorted(dirnames) if d not in _IGNORE_DIR_NAMES]
        for name in sorted(filenames):
            if n >= _MAX_FILES:
                break
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if any(part in _IGNORE_DIR_NAMES for part in Path(rel).parts):
                continue
            suf = p.suffix.lower()
            lang = _EXT_LANG.get(suf, "")
            if not lang:
                continue
            if lf and lang not in lf:
                continue
            files_out.append(
                _extract_file(p, rel, lang, tree_sitter_disabled=tree_sitter_disabled)
            )
            by_lang[lang] = by_lang.get(lang, 0) + 1
            n += 1
        if n >= _MAX_FILES:
            break

    rel_graph: dict[str, Any] = {}
    try:
        rel_graph = build_architecture_map(root, files_out)
    except OSError:
        rel_graph = {"schema": "swarm_relation_graph/v1", "error": "map_failed", "edges": [], "nodes": []}

    conventions = _extract_project_conventions(root, files_out, by_lang)

    ts_n = sum(1 for f in files_out if (f.get("tree_sitter") or {}).get("enabled"))
    return {
        "schema": "swarm_code_analysis/v1",
        "root": str(root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "file_tree": file_tree,
        "files": files_out,
        "relation_graph": rel_graph,
        "stats": {
            "scanned_files": len(files_out),
            "by_language": by_lang,
            "max_files_cap": _MAX_FILES,
            "tree_sitter_files": ts_n,
        },
        "tree_sitter": {
            "enabled": ts_n > 0,
            "files_parsed": ts_n,
            "note": (
                "pip install tree-sitter tree-sitter-language-pack"
                if ts_n == 0
                else "tree-sitter-language-pack"
            ),
        },
        "conventions": conventions,
    }


def analysis_to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Register built-in analyzers (P3-2)
# ---------------------------------------------------------------------------

_analyzer_registry.register("python", _entities_python)
_analyzer_registry.register("javascript", lambda src, path: _entities_js_like(src, path, "javascript"))
_analyzer_registry.register("typescript", lambda src, path: _entities_js_like(src, path, "typescript"))
_analyzer_registry.register("go", _entities_go)
_analyzer_registry.register("php", _entities_php)

# Load plugin analyzers from env: SWARM_CODE_ANALYZER_PLUGINS="my.module,another.mod"
_plugins = os.getenv("SWARM_CODE_ANALYZER_PLUGINS", "").strip()
if _plugins:
    for _plugin_path in _plugins.split(","):
        _pp = _plugin_path.strip()
        if not _pp:
            continue
        try:
            _mod = importlib.import_module(_pp)
            if hasattr(_mod, "register_analyzers"):
                _mod.register_analyzers(_analyzer_registry)
                _logger.info("code_analysis: loaded analyzer plugin %s", _pp)
            else:
                _logger.warning("code_analysis: plugin %s has no register_analyzers()", _pp)
        except Exception as _exc:
            _logger.warning("code_analysis: failed to load plugin %s: %s", _pp, _exc)

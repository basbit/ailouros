from __future__ import annotations

import ast
import importlib
import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from backend.App.workspace.infrastructure.code_analysis.relations import build_architecture_map
from backend.App.workspace.infrastructure.code_analysis.tree_sitter_extract import (
    extract_with_tree_sitter,
)
from backend.App.shared.application.datetime_utils import utc_now_iso
from backend.App.shared.infrastructure.app_config_load import load_app_config_json

_logger = logging.getLogger(__name__)

_DEFAULT_IGNORE_DIRS = (
    ".git,.svn,.hg,.venv,venv,__pycache__,.pytest_cache,.mypy_cache,"
    "node_modules,.idea,.vscode,dist,build,.tox,artifacts"
)


def _load_ignore_dir_names() -> frozenset[str]:
    env_override = os.getenv("SWARM_CODE_ANALYSIS_IGNORE_DIRS", "").strip()
    if env_override:
        return frozenset(d.strip() for d in env_override.split(",") if d.strip())
    base = frozenset(d.strip() for d in _DEFAULT_IGNORE_DIRS.split(",") if d.strip())
    try:
        workspace_policy = load_app_config_json("workspace_ignored_dirs.json")
        extra = workspace_policy.get("ignored_directory_names") or []
        if isinstance(extra, list):
            base = base | frozenset(str(d).strip() for d in extra if str(d).strip())
    except Exception:
        pass
    return base


_IGNORE_DIR_NAMES = _load_ignore_dir_names()

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
    ".gd": "gdscript",
    ".gdscript": "gdscript",
    ".cs": "csharp",
    ".lua": "lua",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".h": "cpp",
    ".c": "c",
    ".swift": "swift",
    ".dart": "dart",
}

_extra_ext = os.getenv("SWARM_EXT_LANG_EXTRA", "").strip()
if _extra_ext:
    for pair in _extra_ext.split(","):
        if ":" in pair:
            ext, lang = pair.split(":", 1)
            _EXT_LANG[ext.strip()] = lang.strip()

_MAX_FILES = int(os.getenv("SWARM_CODE_ANALYSIS_MAX_FILES", "500"))
_MAX_FILE_BYTES = int(os.getenv("SWARM_CODE_ANALYSIS_MAX_FILE_BYTES", "256000"))

AnalyzerFn = Callable[[str, str], list[dict[str, Any]]]


class AnalyzerRegistry:
    """Thin wrapper over :class:`GenericRegistry` for per-language analyzers."""

    def __init__(self) -> None:
        from backend.App.shared.infrastructure.registry import GenericRegistry

        self._inner: GenericRegistry[str, AnalyzerFn] = GenericRegistry(
            name="code-analyzers",
        )

    def register(self, language: str, fn: AnalyzerFn) -> None:
        self._inner.register(language, fn)

    def get(self, language: str) -> AnalyzerFn | None:
        return self._inner.get(language)

    def supported_languages(self) -> list[str]:
        return self._inner.keys_sorted()


_analyzer_registry = AnalyzerRegistry()


def get_analyzer_registry() -> AnalyzerRegistry:
    return _analyzer_registry


def _rel_tree(root: Path, relative_dir: Path) -> dict[str, Any]:
    node_path = root / relative_dir
    if node_path.is_file():
        return {"type": "file", "name": node_path.name}
    children: list[dict[str, Any]] = []
    try:
        for child in sorted(node_path.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.lower())):
            if child.name in _IGNORE_DIR_NAMES:
                continue
            child_relative = relative_dir / child.name
            children.append(_rel_tree(root, child_relative))
    except OSError:
        pass
    return {"type": "dir", "name": node_path.name, "children": children}


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
    for match in _RE_EXPORT_FN.finditer(source):
        out.append({"kind": "function", "name": match.group(1), "line": source[: match.start()].count("\n") + 1})
    for match in _RE_CONST_FN.finditer(source):
        out.append({"kind": "function", "name": match.group(1), "line": source[: match.start()].count("\n") + 1})
    for match in _RE_CLASS.finditer(source):
        out.append({"kind": "class", "name": match.group(1), "line": source[: match.start()].count("\n") + 1})
    for match in _RE_ROUTE.finditer(source):
        out.append({"kind": "route", "path": match.group(1), "line": source[: match.start()].count("\n") + 1})
    for match in _RE_FLASK.finditer(source):
        out.append({"kind": "route", "path": match.group(1), "line": source[: match.start()].count("\n") + 1})
    for match in _RE_FASTAPI.finditer(source):
        out.append({"kind": "route", "path": match.group(1), "line": source[: match.start()].count("\n") + 1})
    default_export_match = re.search(r"export\s+default\s+(?:function\s+)?(\w+)", source)
    if default_export_match:
        out.append(
            {
                "kind": "component",
                "name": default_export_match.group(1),
                "line": source[: default_export_match.start()].count("\n") + 1,
            }
        )
    return out


_RE_GO_FUNC = re.compile(r"^func\s+(?:\([^)]+\)\s*)?(\w+)\s*\(", re.MULTILINE)
_RE_GO_TYPE = re.compile(r"^type\s+(\w+)\s+", re.MULTILINE)


def _entities_go(source: str, path: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for match in _RE_GO_FUNC.finditer(source):
        out.append({"kind": "function", "name": match.group(1), "line": source[: match.start()].count("\n") + 1})
    for match in _RE_GO_TYPE.finditer(source):
        out.append({"kind": "type", "name": match.group(1), "line": source[: match.start()].count("\n") + 1})
    return out


_RE_PHP_CLASS = re.compile(r"class\s+(\w+)", re.MULTILINE)
_RE_PHP_FUNC = re.compile(r"function\s+(\w+)\s*\(", re.MULTILINE)


def _entities_php(source: str, path: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for match in _RE_PHP_CLASS.finditer(source):
        out.append({"kind": "class", "name": match.group(1), "line": source[: match.start()].count("\n") + 1})
    for match in _RE_PHP_FUNC.finditer(source):
        out.append({"kind": "function", "name": match.group(1), "line": source[: match.start()].count("\n") + 1})
    return out


_CSHARP_NAMESPACE_PATTERN = re.compile(r"^\s*namespace\s+([A-Za-z_][\w.]*)", re.MULTILINE)
_CSHARP_TYPE_PATTERN = re.compile(
    r"^\s*(?:\[[^\]]+\]\s*)*"
    r"(?:(?:public|private|protected|internal|static|abstract|sealed|partial|readonly|unsafe|new)\s+)*"
    r"(class|struct|interface|enum|record)\s+([A-Za-z_]\w*)",
    re.MULTILINE,
)
_CSHARP_METHOD_PATTERN = re.compile(
    r"^\s*(?:\[[^\]]+\]\s*)*"
    r"(?:(?:public|private|protected|internal|static|abstract|virtual|override|sealed|async|unsafe|new)\s+)*"
    r"(?:[A-Za-z_][\w<>,\[\].?]*\s+)+"
    r"([A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)
_CSHARP_SERIALIZED_FIELD_PATTERN = re.compile(
    r"^\s*(?:\[[^\]]+\]\s*)*"
    r"(?:(?:public|private|protected|internal|static|readonly|const|serializedfield)\s+)*"
    r"[A-Za-z_][\w<>,\[\].?]*\s+([A-Za-z_]\w*)\s*(?:=|;)",
    re.IGNORECASE | re.MULTILINE,
)
_CSHARP_CONTROL_WORDS = frozenset({"if", "for", "foreach", "while", "switch", "catch", "using", "lock"})


def _entities_csharp(source: str, path: str) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for namespace_match in _CSHARP_NAMESPACE_PATTERN.finditer(source):
        entities.append({
            "kind": "namespace",
            "name": namespace_match.group(1),
            "line": source[: namespace_match.start()].count("\n") + 1,
        })
    for type_match in _CSHARP_TYPE_PATTERN.finditer(source):
        entities.append({
            "kind": type_match.group(1),
            "name": type_match.group(2),
            "line": source[: type_match.start()].count("\n") + 1,
        })
    for method_match in _CSHARP_METHOD_PATTERN.finditer(source):
        method_name = method_match.group(1)
        if method_name not in _CSHARP_CONTROL_WORDS:
            entities.append({
                "kind": "method",
                "name": method_name,
                "line": source[: method_match.start()].count("\n") + 1,
            })
    for field_match in _CSHARP_SERIALIZED_FIELD_PATTERN.finditer(source):
        entities.append({
            "kind": "field",
            "name": field_match.group(1),
            "line": source[: field_match.start()].count("\n") + 1,
        })
    return entities


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

    analyzer = _analyzer_registry.get(lang)
    if analyzer:
        entities = analyzer(text, rel)
    else:
        entities = [{"kind": "file", "name": Path(rel).name, "line": 1}]

    return {"path": rel, "language": lang, "entities": entities}


def _extract_signature(source: str, entity: dict[str, Any], max_lines: int = 12) -> str:
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
    files_by_entity_count = sorted(
        [f for f in files_out if len(f.get("entities") or []) >= 2],
        key=lambda f: len(f.get("entities") or []),
        reverse=True,
    )
    signatures: list[str] = []
    for f in files_by_entity_count[:8]:
        file_path = root / f["path"]
        if not file_path.is_file():
            continue
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for entity in (f.get("entities") or []):
            if entity.get("kind") in ("class", "type") and len(signatures) < 5:
                sig = _extract_signature(source, entity, max_lines=12)
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

    language_filter_set = {x.strip().lower() for x in languages_filter} if languages_filter else None
    file_tree = _rel_tree(root, Path("."))
    files_out: list[dict[str, Any]] = []
    by_lang: dict[str, int] = {}
    file_count = 0

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in sorted(dirnames) if d not in _IGNORE_DIR_NAMES]
        for name in sorted(filenames):
            if file_count >= _MAX_FILES:
                break
            file_path = Path(dirpath) / name
            relative_path = file_path.relative_to(root).as_posix()
            if any(part in _IGNORE_DIR_NAMES for part in Path(relative_path).parts):
                continue
            file_suffix = file_path.suffix.lower()
            lang = _EXT_LANG.get(file_suffix, "")
            if not lang:
                continue
            if language_filter_set and lang not in language_filter_set:
                continue
            files_out.append(
                _extract_file(file_path, relative_path, lang, tree_sitter_disabled=tree_sitter_disabled)
            )
            by_lang[lang] = by_lang.get(lang, 0) + 1
            file_count += 1
        if file_count >= _MAX_FILES:
            break

    rel_graph: dict[str, Any] = {}
    try:
        rel_graph = build_architecture_map(root, files_out)
    except OSError:
        rel_graph = {"schema": "swarm_relation_graph/v1", "error": "map_failed", "edges": [], "nodes": []}

    conventions = _extract_project_conventions(root, files_out, by_lang)

    tree_sitter_file_count = sum(1 for f in files_out if (f.get("tree_sitter") or {}).get("enabled"))
    return {
        "schema": "swarm_code_analysis/v1",
        "root": str(root),
        "generated_at": utc_now_iso(),
        "file_tree": file_tree,
        "files": files_out,
        "relation_graph": rel_graph,
        "stats": {
            "scanned_files": len(files_out),
            "by_language": by_lang,
            "max_files_cap": _MAX_FILES,
            "tree_sitter_files": tree_sitter_file_count,
        },
        "tree_sitter": {
            "enabled": tree_sitter_file_count > 0,
            "files_parsed": tree_sitter_file_count,
            "note": (
                "pip install tree-sitter tree-sitter-language-pack"
                if tree_sitter_file_count == 0
                else "tree-sitter-language-pack"
            ),
        },
        "conventions": conventions,
    }


def analysis_to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


_analyzer_registry.register("python", _entities_python)
_analyzer_registry.register("javascript", lambda src, path: _entities_js_like(src, path, "javascript"))
_analyzer_registry.register("typescript", lambda src, path: _entities_js_like(src, path, "typescript"))
_analyzer_registry.register("go", _entities_go)
_analyzer_registry.register("php", _entities_php)
_analyzer_registry.register("csharp", _entities_csharp)

_plugins_raw = os.getenv("SWARM_CODE_ANALYZER_PLUGINS", "").strip()
if _plugins_raw:
    for _plugin_module_path in _plugins_raw.split(","):
        _plugin_module_name = _plugin_module_path.strip()
        if not _plugin_module_name:
            continue
        try:
            _plugin_module = importlib.import_module(_plugin_module_name)
            if hasattr(_plugin_module, "register_analyzers"):
                _plugin_module.register_analyzers(_analyzer_registry)
                _logger.info("code_analysis: loaded analyzer plugin %s", _plugin_module_name)
            else:
                raise AttributeError(
                    f"plugin {_plugin_module_name} has no register_analyzers()"
                )
        except (AttributeError, ImportError) as _plugin_exc:
            raise RuntimeError(
                "code analysis plugin load failed: operation=load_plugin "
                f"module={_plugin_module_name!r} expected=register_analyzers actual={_plugin_exc}"
            ) from _plugin_exc

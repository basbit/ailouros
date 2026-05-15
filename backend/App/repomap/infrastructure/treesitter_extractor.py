from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, cast

from backend.App.repomap.domain.symbol_graph import SymbolEdge, SymbolGraph, SymbolNode

_SUFFIX_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".vue": "vue",
}

_SOURCE_SUFFIXES = frozenset(_SUFFIX_TO_LANG)


class RepoMapExtractionError(RuntimeError):
    pass


def _ignore_dir_names() -> frozenset[str]:
    from backend.App.workspace.infrastructure.workspace_io import _IGNORE_DIR_NAMES
    return _IGNORE_DIR_NAMES


def _ts_available() -> bool:
    try:
        import tree_sitter_language_pack as _p
        del _p
        return True
    except ImportError:
        return False


def _parse_tree(source: bytes, grammar: str) -> Any:
    import tree_sitter
    from tree_sitter_language_pack import get_language
    lang = get_language(cast(Any, grammar))
    return tree_sitter.Parser(lang).parse(source), lang


def _node_text(node: Any, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _node_line(node: Any) -> int:
    return int(node.start_point[0]) + 1


def _extract_python_symbols(
    source: bytes, rel: str
) -> list[tuple[str, str, int, int]]:
    from tree_sitter import Query
    tree, lang = _parse_tree(source, "python")
    q = Query(
        lang,
        "(function_definition name: (identifier) @fn)\n"
        "(class_definition name: (identifier) @cls)",
    )
    caps = q.captures(tree.root_node)
    results: list[tuple[str, str, int, int]] = []
    for node in caps.get("fn", []):
        p = node.parent
        kind = "method" if p and p.parent and p.parent.type == "class_definition" else "function"
        results.append((kind, _node_text(node, source), _node_line(node), node.end_point[0] + 1))
    for node in caps.get("cls", []):
        results.append(("class", _node_text(node, source), _node_line(node), node.end_point[0] + 1))
    return results


def _extract_js_ts_symbols(
    source: bytes, grammar: str
) -> list[tuple[str, str, int, int]]:
    from tree_sitter import Query
    tree, lang = _parse_tree(source, grammar)
    if grammar in ("typescript", "tsx"):
        qsrc = (
            "(function_declaration name: (identifier) @fn)\n"
            "(class_declaration name: (type_identifier) @cls)\n"
            "(method_definition name: (property_identifier) @m)"
        )
    else:
        qsrc = (
            "(function_declaration name: (identifier) @fn)\n"
            "(class_declaration name: (identifier) @cls)\n"
            "(method_definition name: (property_identifier) @m)"
        )
    q = Query(lang, qsrc)
    caps = q.captures(tree.root_node)
    results: list[tuple[str, str, int, int]] = []
    for tag, nodes_list in (("fn", caps.get("fn", [])), ("cls", caps.get("cls", [])), ("m", caps.get("m", []))):
        kind = "function" if tag in ("fn", "m") else "class"
        for node in nodes_list:
            results.append((kind, _node_text(node, source), _node_line(node), node.end_point[0] + 1))
    return results


def _extract_vue_symbols(source: bytes) -> list[tuple[str, str, int, int]]:
    match = re.search(r"<script[^>]*>([\s\S]*?)</script>", source.decode("utf-8", errors="replace"), re.IGNORECASE)
    script_src = match.group(1).encode("utf-8") if match else source
    return _extract_js_ts_symbols(script_src, "typescript")


def _extract_symbols_from_file(
    file_path: Path,
    source: bytes,
    lang: str,
) -> list[tuple[str, str, int, int]]:
    try:
        if lang == "python":
            return _extract_python_symbols(source, str(file_path))
        if lang in ("javascript", "typescript", "tsx"):
            return _extract_js_ts_symbols(source, lang)
        if lang == "vue":
            return _extract_vue_symbols(source)
        return []
    except (LookupError, OSError, ValueError, RuntimeError, UnicodeDecodeError) as exc:
        raise RepoMapExtractionError(
            f"tree-sitter parse failed for {file_path}: {exc}"
        ) from exc


def _collect_defined_names(
    nodes: list[SymbolNode],
) -> dict[str, list[str]]:
    name_to_files: dict[str, list[str]] = {}
    for node in nodes:
        name_to_files.setdefault(node.name, []).append(node.file_path)
    return name_to_files


def _build_import_edges(
    file_path: str,
    source: bytes,
    name_to_files: dict[str, list[str]],
) -> list[SymbolEdge]:
    text = source.decode("utf-8", errors="replace")
    edges: list[SymbolEdge] = []
    seen: set[tuple[str, str]] = set()

    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", text):
        name = match.group(1)
        targets = name_to_files.get(name)
        if not targets:
            continue
        for target_file in targets:
            if target_file == file_path:
                continue
            key = (file_path, target_file)
            if key in seen:
                continue
            seen.add(key)
            edges.append(SymbolEdge(from_node_path=file_path, to_node_path=target_file, weight=1.0))
    return edges


def extract_symbols(workspace_root: Path) -> SymbolGraph:
    if not _ts_available():
        raise RepoMapExtractionError(
            "tree-sitter-language-pack is not installed; cannot extract symbols"
        )

    ignore_dirs = _ignore_dir_names()
    nodes: list[SymbolNode] = []
    file_sources: dict[str, bytes] = {}

    for dirpath, dirnames, filenames in os.walk(workspace_root, topdown=True):
        dirnames[:] = [d for d in sorted(dirnames) if d not in ignore_dirs]
        for name in sorted(filenames):
            suffix = Path(name).suffix.lower()
            if suffix not in _SOURCE_SUFFIXES:
                continue
            full = Path(dirpath) / name
            try:
                source = full.read_bytes()
            except OSError as exc:
                raise RepoMapExtractionError(f"cannot read {full}: {exc}") from exc

            rel = full.relative_to(workspace_root).as_posix()
            lang = _SUFFIX_TO_LANG[suffix]

            try:
                raw_symbols = _extract_symbols_from_file(full, source, lang)
            except RepoMapExtractionError:
                raise

            for kind, sym_name, line_start, line_end in raw_symbols:
                nodes.append(
                    SymbolNode(
                        file_path=rel,
                        kind=cast(Any, kind),
                        name=sym_name,
                        line_start=line_start,
                        line_end=line_end,
                    )
                )
            file_sources[rel] = source

    name_to_files = _collect_defined_names(nodes)
    edges: list[SymbolEdge] = []
    for rel, source in file_sources.items():
        edges.extend(_build_import_edges(rel, source, name_to_files))

    return SymbolGraph(nodes=tuple(nodes), edges=tuple(edges))


def _signature_for_node(node: SymbolNode) -> str:
    prefix = {"function": "def", "method": "def", "class": "class"}[node.kind]
    return f"{prefix} {node.name}  # L{node.line_start}"


def signatures_by_file(nodes: tuple[SymbolNode, ...]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for node in nodes:
        result.setdefault(node.file_path, []).append(_signature_for_node(node))
    return result

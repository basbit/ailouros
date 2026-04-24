from __future__ import annotations

import os
import re
from typing import Any, Optional, cast

_TS_DISABLED = os.getenv("SWARM_TREE_SITTER_DISABLE", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def _pack_available() -> bool:
    if _TS_DISABLED:
        return False
    try:
        import tree_sitter_language_pack as _tslp_check
        del _tslp_check
    except ImportError:
        return False
    return True


_LANG_TO_GRAMMAR: dict[str, str] = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "go": "go",
    "php": "php",
    "ruby": "ruby",
    "java": "java",
    "kotlin": "kotlin",
    "rust": "rust",
    "vue": "vue",
}

_extra_grammars = os.environ.get("SWARM_TREE_SITTER_GRAMMARS_EXTRA", "").strip()
if _extra_grammars:
    for pair in _extra_grammars.split(","):
        if ":" in pair:
            lang, grammar = pair.split(":", 1)
            _LANG_TO_GRAMMAR[lang.strip()] = grammar.strip()


def _grammar_name(swarm_lang: str, rel_path: str) -> Optional[str]:
    if swarm_lang == "typescript" and rel_path.lower().endswith(".tsx"):
        return "tsx"
    return _LANG_TO_GRAMMAR.get(swarm_lang)


def _node_line(node) -> int:
    return int(node.start_point[0]) + 1


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _dedupe_entities(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for entity in items:
        key = (entity.get("kind"), entity.get("name") or entity.get("path"), entity.get("line"))
        if key in seen:
            continue
        seen.add(key)
        out.append(entity)
    return out


def _extract_python(source: bytes, rel: str) -> list[dict[str, Any]]:
    from tree_sitter import Query
    from tree_sitter_language_pack import get_language

    lang = get_language("python")
    tree = __import__("tree_sitter").Parser(lang).parse(source)
    q = Query(
        lang,
        """
(function_definition name: (identifier) @name)
(class_definition name: (identifier) @name)
""",
    )
    caps = q.captures(tree.root_node)
    out: list[dict[str, Any]] = []
    for node in caps.get("name", []):
        parent = node.parent
        if parent is None:
            continue
        kind = "class" if parent.type == "class_definition" else "function"
        out.append({"kind": kind, "name": _node_text(node, source), "line": _node_line(node)})
    return out


def _extract_go(source: bytes, rel: str) -> list[dict[str, Any]]:
    from tree_sitter import Query
    from tree_sitter_language_pack import get_language

    lang = get_language("go")
    tree = __import__("tree_sitter").Parser(lang).parse(source)
    q = Query(
        lang,
        """
(function_declaration name: (identifier) @name)
(method_declaration name: (field_identifier) @name)
""",
    )
    caps = q.captures(tree.root_node)
    out: list[dict[str, Any]] = []
    for node in caps.get("name", []):
        kind = "method" if node.parent and node.parent.type == "method_declaration" else "function"
        out.append({"kind": kind, "name": _node_text(node, source), "line": _node_line(node)})
    return out


def _extract_php(source: bytes, rel: str) -> list[dict[str, Any]]:
    from tree_sitter import Query
    from tree_sitter_language_pack import get_language

    lang = get_language("php")
    tree = __import__("tree_sitter").Parser(lang).parse(source)
    q = Query(
        lang,
        """
(class_declaration name: (name) @cls)
(function_definition name: (name) @fn)
(method_declaration name: (name) @m)
""",
    )
    caps = q.captures(tree.root_node)
    out: list[dict[str, Any]] = []
    for node in caps.get("cls", []):
        out.append({"kind": "class", "name": _node_text(node, source), "line": _node_line(node)})
    for node in caps.get("fn", []):
        out.append({"kind": "function", "name": _node_text(node, source), "line": _node_line(node)})
    for node in caps.get("m", []):
        out.append({"kind": "function", "name": _node_text(node, source), "line": _node_line(node)})
    return out


def _extract_js_ts(source: bytes, rel: str, grammar: str) -> list[dict[str, Any]]:
    from tree_sitter import Query
    from tree_sitter_language_pack import get_language

    lang = get_language(cast(Any, grammar))
    tree = __import__("tree_sitter").Parser(lang).parse(source)
    if grammar == "javascript":
        qsrc = """
(function_declaration name: (identifier) @fn)
(class_declaration name: (identifier) @cls)
(method_definition name: (property_identifier) @m)
"""
    else:
        qsrc = """
(function_declaration name: (identifier) @fn)
(class_declaration name: (type_identifier) @cls)
(method_definition name: (property_identifier) @m)
"""
    q = Query(lang, qsrc)
    caps = q.captures(tree.root_node)
    out: list[dict[str, Any]] = []
    for node in caps.get("fn", []):
        out.append({"kind": "function", "name": _node_text(node, source), "line": _node_line(node)})
    for node in caps.get("cls", []):
        out.append({"kind": "class", "name": _node_text(node, source), "line": _node_line(node)})
    for node in caps.get("m", []):
        out.append({"kind": "function", "name": _node_text(node, source), "line": _node_line(node)})
    return out


def _merge_route_heuristics(
    text: str,
    rel: str,
    swarm_lang: str,
    base: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from backend.App.workspace.infrastructure.code_analysis.scan import _entities_js_like

    extra: list[dict[str, Any]] = []
    if swarm_lang in ("javascript", "typescript"):
        for entity in _entities_js_like(text, rel, swarm_lang):
            if entity.get("kind") in ("route", "component"):
                extra.append(entity)
    return _dedupe_entities(base + extra)


def extract_with_tree_sitter(
    source: str,
    rel: str,
    swarm_lang: str,
    *,
    disabled: bool = False,
) -> Optional[dict[str, Any]]:
    if disabled or not _pack_available():
        return None
    grammar = _grammar_name(swarm_lang, rel)
    if not grammar:
        return None
    raw = source.encode("utf-8")
    try:
        if grammar == "python":
            entities = _dedupe_entities(_extract_python(raw, rel))
            return {"entities": entities, "grammar": grammar, "parser": "tree-sitter"}
        if grammar == "go":
            entities = _dedupe_entities(_extract_go(raw, rel))
            return {"entities": entities, "grammar": grammar, "parser": "tree-sitter"}
        if grammar == "php":
            entities = _extract_php(raw, rel)
            entities = _merge_route_heuristics(source, rel, swarm_lang, entities)
            entities = _dedupe_entities(entities)
            return {"entities": entities, "grammar": grammar, "parser": "tree-sitter"}
        elif grammar in ("javascript", "typescript", "tsx"):
            entities = _extract_js_ts(raw, rel, grammar)
            entities = _merge_route_heuristics(source, rel, swarm_lang, entities)
            entities = _dedupe_entities(entities)
            return {"entities": entities, "grammar": grammar, "parser": "tree-sitter"}
        elif grammar == "vue":
            match = re.search(
                r"<script[^>]*>([\s\S]*?)</script>",
                source,
                re.IGNORECASE,
            )
            script = match.group(1) if match else source
            entities = _extract_js_ts(script.encode("utf-8"), rel, "typescript")
            entities = _merge_route_heuristics(source, rel, "typescript", entities)
            entities = _dedupe_entities(entities)
            return {"entities": entities, "grammar": "vue+ts", "parser": "tree-sitter"}
        return None
    except (LookupError, OSError, ValueError, RuntimeError):
        return None

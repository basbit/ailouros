from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_PY_FROM = re.compile(
    r"^\s*from\s+([\w.]+)\s+import\s+",
    re.MULTILINE,
)
_PY_IMPORT = re.compile(r"^\s*import\s+([\w.]+(?:\s*,\s*[\w.]+)*)", re.MULTILINE)
_JS_IMP = re.compile(
    r"""^\s*import\s+(?:(?:\{[^}]+\}|\*\s+as\s+\w+|\w+)\s+from\s+)?['"]([^'"]+)['"]""",
    re.MULTILINE,
)
_GO_IMP = re.compile(r'^\s*import\s+(?:\w+\s+)?"([^"]+)"', re.MULTILINE)
_PHP_USE = re.compile(r"^\s*use\s+([^;]+);", re.MULTILINE)
_CALL_LIKE = re.compile(r"\b([A-Za-z_][\w]*)\s*\(")


def _entity_edges(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for f in files:
        path_str = str(f.get("path") or "")
        if not path_str:
            continue
        for entity in f.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            kind = entity.get("kind")
            name_text = str(entity.get("name") or "").strip()
            if kind == "route":
                edges.append(
                    {
                        "source": path_str,
                        "target": name_text or "?",
                        "kind": "route",
                        "detail": entity.get("method") or "",
                        "line": entity.get("line"),
                    }
                )
    return edges


def _resolve_py_module(root: Path, mod: str, current_rel: str) -> str | None:
    parts = mod.split(".")
    if not parts:
        return None
    cur_dir = (root / current_rel).parent if current_rel else root
    if mod.startswith("."):
        return None
    for base in (root, cur_dir):
        candidate = base.joinpath(*parts)
        if (candidate.with_suffix(".py")).is_file():
            try:
                return candidate.with_suffix(".py").relative_to(root).as_posix()
            except ValueError:
                return None
        init_py = base.joinpath(*parts, "__init__.py")
        if init_py.is_file():
            try:
                return init_py.relative_to(root).as_posix()
            except ValueError:
                return None
    rel = "/".join(parts) + ".py"
    if (root / rel).is_file():
        return rel
    return None


_IMPORT_EDGE_MAX_FILE_BYTES = int(os.environ.get("SWARM_IMPORT_EDGE_MAX_FILE_BYTES", "48000"))

_SKIP_IMPORT_LANGUAGES = frozenset(
    lang.strip() for lang in os.environ.get("SWARM_SKIP_IMPORT_LANGUAGES", "").split(",") if lang.strip()
)


def _import_edges(
    root: Path,
    files: list[dict[str, Any]],
    max_file_bytes: int = _IMPORT_EDGE_MAX_FILE_BYTES,
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    root = root.resolve()
    path_set = {str(f.get("path")) for f in files if f.get("path")}

    for f in files:
        rel = str(f.get("path") or "")
        lang = str(f.get("language") or "")
        if not rel or lang in _SKIP_IMPORT_LANGUAGES:
            continue
        p = root / rel
        if not p.is_file():
            continue
        try:
            raw = p.read_bytes()[:max_file_bytes]
            text = raw.decode("utf-8", errors="replace")
        except OSError:
            continue

        if lang == "python":
            for match in _PY_FROM.finditer(text):
                mod = match.group(1)
                target = _resolve_py_module(root, mod, rel)
                if target and target in path_set:
                    edges.append(
                        {"source": rel, "target": target, "kind": "imports", "detail": mod}
                    )
            for match in _PY_IMPORT.finditer(text):
                chunk = match.group(1)
                for part in re.split(r"\s*,\s*", chunk):
                    part = part.strip().split(" as ")[0].strip()
                    if not part or "." not in part:
                        continue
                    target = _resolve_py_module(root, part, rel)
                    if target and target in path_set:
                        edges.append(
                            {"source": rel, "target": target, "kind": "imports", "detail": part}
                        )

        elif lang in ("javascript", "typescript"):
            for match in _JS_IMP.finditer(text):
                spec = match.group(1).strip()
                if spec.startswith(".") or spec.startswith("/"):
                    base = (root / rel).parent
                    candidate = (base / spec).resolve()
                    try:
                        r2 = candidate.relative_to(root).as_posix()
                    except ValueError:
                        continue
                    for ext in ("", ".ts", ".tsx", ".js", ".jsx"):
                        trial = r2 + ext if ext else r2
                        if trial in path_set:
                            edges.append(
                                {"source": rel, "target": trial, "kind": "imports", "detail": spec}
                            )
                            break

        elif lang == "go":
            for match in _GO_IMP.finditer(text):
                imp = match.group(1)
                if imp.startswith("."):
                    continue
                edges.append({"source": rel, "target": imp, "kind": "go_import", "detail": imp})

        elif lang == "php":
            for match in _PHP_USE.finditer(text):
                use_line = match.group(1).strip()
                first = use_line.split(",")[0].strip().split()[0]
                if "\\" in first:
                    pseudo = first.lstrip("\\").replace("\\", "/") + ".php"
                    if pseudo in path_set:
                        edges.append(
                            {"source": rel, "target": pseudo, "kind": "uses", "detail": first}
                        )

    return edges


def _call_edges_light(root: Path, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from collections import defaultdict

    by_name: dict[str, list[str]] = defaultdict(list)
    for f in files:
        rel = str(f.get("path") or "")
        for entity in f.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            if entity.get("kind") != "function":
                continue
            name_text = str(entity.get("name") or "").strip()
            if len(name_text) < 3:
                continue
            by_name[name_text].append(rel)

    name_to_file: dict[str, str] = {}
    for name_text, paths in by_name.items():
        unique_paths = sorted(set(paths))
        if len(unique_paths) == 1:
            name_to_file[name_text] = unique_paths[0]

    edges: list[dict[str, Any]] = []

    for f in files:
        rel = str(f.get("path") or "")
        if not rel:
            continue
        p = root / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:40_000]
        except OSError:
            continue
        seen: set[str] = set()
        for match in _CALL_LIKE.finditer(text):
            name_text = match.group(1)
            if name_text not in name_to_file or name_to_file[name_text] == rel:
                continue
            key = f"{name_text}@{name_to_file[name_text]}"
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                {
                    "source": rel,
                    "target": name_to_file[name_text],
                    "kind": "calls_name",
                    "detail": name_text,
                }
            )
    return edges


def build_architecture_map(root: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
    root = root.expanduser().resolve()
    edges: list[dict[str, Any]] = []
    edges.extend(_entity_edges(files))
    edges.extend(_import_edges(root, files))
    edges.extend(_call_edges_light(root, files))
    nodes = [{"id": f["path"], "language": f.get("language")} for f in files if f.get("path")]
    return {
        "schema": "swarm_relation_graph/v1",
        "nodes": nodes,
        "edges": edges,
        "stats": {"edge_count": len(edges), "node_count": len(nodes)},
    }

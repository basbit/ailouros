"""File dependency graph: entities + import/call heuristics (no mandatory tree-sitter)."""

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
# Слабая эвристика вызова функций: foo( — только для имён из соседних файлов (см. ниже)
_CALL_LIKE = re.compile(r"\b([A-Za-z_][\w]*)\s*\(")


def _entity_edges(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for f in files:
        p = str(f.get("path") or "")
        if not p:
            continue
        for e in f.get("entities") or []:
            if not isinstance(e, dict):
                continue
            k = e.get("kind")
            nm = str(e.get("name") or "").strip()
            if k == "route":
                edges.append(
                    {
                        "source": p,
                        "target": nm or "?",
                        "kind": "route",
                        "detail": e.get("method") or "",
                        "line": e.get("line"),
                    }
                )
    return edges


def _resolve_py_module(root: Path, mod: str, current_rel: str) -> str | None:
    """mod 'a.b.c' -> относительный путь или None."""
    parts = mod.split(".")
    if not parts:
        return None
    cur_dir = (root / current_rel).parent if current_rel else root
    # относительный import .foo — упрощённо пропускаем
    if mod.startswith("."):
        return None
    for base in (root, cur_dir):
        cand = base.joinpath(*parts)
        if (cand.with_suffix(".py")).is_file():
            try:
                return cand.with_suffix(".py").relative_to(root).as_posix()
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

# Languages to skip in import analysis (env: SWARM_SKIP_IMPORT_LANGUAGES="ruby,kotlin")
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
            for m in _PY_FROM.finditer(text):
                mod = m.group(1)
                tgt = _resolve_py_module(root, mod, rel)
                if tgt and tgt in path_set:
                    edges.append(
                        {"source": rel, "target": tgt, "kind": "imports", "detail": mod}
                    )
            for m in _PY_IMPORT.finditer(text):
                chunk = m.group(1)
                for part in re.split(r"\s*,\s*", chunk):
                    part = part.strip().split(" as ")[0].strip()
                    if not part or "." not in part:
                        continue
                    tgt = _resolve_py_module(root, part, rel)
                    if tgt and tgt in path_set:
                        edges.append(
                            {"source": rel, "target": tgt, "kind": "imports", "detail": part}
                        )

        elif lang in ("javascript", "typescript"):
            for m in _JS_IMP.finditer(text):
                spec = m.group(1).strip()
                if spec.startswith(".") or spec.startswith("/"):
                    # относительный путь
                    base = (root / rel).parent
                    cand = (base / spec).resolve()
                    try:
                        r2 = cand.relative_to(root).as_posix()
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
            for m in _GO_IMP.finditer(text):
                imp = m.group(1)
                if imp.startswith("."):
                    continue
                edges.append({"source": rel, "target": imp, "kind": "go_import", "detail": imp})

        elif lang == "php":
            for m in _PHP_USE.finditer(text):
                use_line = m.group(1).strip()
                first = use_line.split(",")[0].strip().split()[0]
                if "\\" in first:
                    pseudo = first.lstrip("\\").replace("\\", "/") + ".php"
                    if pseudo in path_set:
                        edges.append(
                            {"source": rel, "target": pseudo, "kind": "uses", "detail": first}
                        )

    return edges


def _call_edges_light(root: Path, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Связи «вызов по имени» только если имя функции встречается ровно в одном файле проекта."""
    from collections import defaultdict

    by_name: dict[str, list[str]] = defaultdict(list)
    for f in files:
        rel = str(f.get("path") or "")
        for e in f.get("entities") or []:
            if not isinstance(e, dict):
                continue
            if e.get("kind") != "function":
                continue
            nm = str(e.get("name") or "").strip()
            if len(nm) < 3:
                continue
            by_name[nm].append(rel)

    name_to_file: dict[str, str] = {}
    for nm, paths in by_name.items():
        uniq = sorted(set(paths))
        if len(uniq) == 1:
            name_to_file[nm] = uniq[0]

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
        for m in _CALL_LIKE.finditer(text):
            nm = m.group(1)
            if nm not in name_to_file or name_to_file[nm] == rel:
                continue
            key = f"{nm}@{name_to_file[nm]}"
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                {
                    "source": rel,
                    "target": name_to_file[nm],
                    "kind": "calls_name",
                    "detail": nm,
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

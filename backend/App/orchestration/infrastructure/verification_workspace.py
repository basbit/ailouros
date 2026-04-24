
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


_EXCLUDE_DIRS = {"test", "tests", "vendor", "node_modules", ".venv", "__pycache__", ".git"}


def normalize_workspace_path(root: Path, path: str) -> str:
    raw = str(path or "").strip().strip("`").strip().strip("\"'")
    if not raw:
        return ""
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return candidate.as_posix()
    return Path(raw).as_posix().lstrip("./")


def collect_path_existence(
    workspace_root: str,
    *,
    manifest_files: list[str] | None = None,
    must_exist_files: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    root = Path(workspace_root)
    manifest_states: list[dict[str, Any]] = []
    required_states: list[dict[str, Any]] = []

    seen_manifest: set[str] = set()
    for path in manifest_files or []:
        rel = normalize_workspace_path(root, path)
        if not rel or rel in seen_manifest:
            continue
        seen_manifest.add(rel)
        fpath = root / rel if not os.path.isabs(rel) else Path(rel)
        manifest_states.append({"path": rel or str(path), "exists": fpath.exists()})

    seen_required: set[str] = set()
    for path in must_exist_files or []:
        rel = normalize_workspace_path(root, path)
        if not rel or rel in seen_required:
            continue
        seen_required.add(rel)
        fpath = root / rel if not os.path.isabs(rel) else Path(rel)
        required_states.append({"path": rel or str(path), "exists": fpath.exists()})

    return {"manifest": manifest_states, "required": required_states}


def collect_symbol_presence(workspace_root: str, spec_symbols: list[str] | None = None) -> dict[str, bool]:
    root = Path(workspace_root) if workspace_root else None
    result: dict[str, bool] = {}
    if not root or not root.exists():
        return result
    for symbol in spec_symbols or []:
        found = False
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if any(part in _EXCLUDE_DIRS for part in file_path.parts):
                continue
            try:
                if symbol in file_path.read_text(errors="replace"):
                    found = True
                    break
            except OSError:
                continue
        result[str(symbol)] = found
    return result


def collect_php_autoload_namespace_findings(workspace_root: str) -> list[dict[str, Any]]:
    root = Path(workspace_root) if workspace_root else None
    if not root or not root.exists():
        return []

    findings: list[dict[str, Any]] = []
    composer_files = [
        path for path in root.rglob("composer.json")
        if path.is_file() and "vendor" not in path.parts
    ]
    namespace_re = re.compile(r"^\s*namespace\s+([^;]+);", re.MULTILINE)
    class_re = re.compile(r"\b(class|interface|trait|enum)\s+[A-Za-z_][A-Za-z0-9_]*")

    for composer_path in composer_files:
        try:
            payload = json.loads(composer_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        autoload = payload.get("autoload") if isinstance(payload, dict) else None
        psr4 = autoload.get("psr-4") if isinstance(autoload, dict) else None
        if not isinstance(psr4, dict):
            continue
        composer_root = composer_path.parent
        for prefix, raw_dirs in psr4.items():
            declared_prefix = str(prefix or "").strip().rstrip("\\")
            if not declared_prefix:
                continue
            dirs = raw_dirs if isinstance(raw_dirs, list) else [raw_dirs]
            for raw_dir in dirs:
                mapped_dir = composer_root / str(raw_dir or "").strip()
                if not mapped_dir.exists() or not mapped_dir.is_dir():
                    continue
                for php_file in mapped_dir.rglob("*.php"):
                    if not php_file.is_file():
                        continue
                    if any(part in _EXCLUDE_DIRS for part in php_file.parts):
                        continue
                    try:
                        content = php_file.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    if not class_re.search(content):
                        continue
                    rel_inside = php_file.relative_to(mapped_dir)
                    expected_parts = [part for part in rel_inside.parts[:-1] if part and part != "."]
                    expected_namespace = "\\".join([declared_prefix, *expected_parts]).strip("\\")
                    match = namespace_re.search(content)
                    actual_namespace = str(match.group(1)).strip("\\").strip() if match else ""
                    if not actual_namespace:
                        findings.append({
                            "path": php_file.relative_to(root).as_posix(),
                            "error": "MISSING_NAMESPACE_DECLARATION",
                            "expected_namespace": expected_namespace,
                            "actual_namespace": "",
                        })
                        continue
                    if actual_namespace != expected_namespace:
                        findings.append({
                            "path": php_file.relative_to(root).as_posix(),
                            "error": "AUTOLOAD_NAMESPACE_PATH_MISMATCH",
                            "expected_namespace": expected_namespace,
                            "actual_namespace": actual_namespace,
                        })
    return findings


def scan_stub_findings(
    workspace_root: str,
    *,
    changed_files: list[str] | None = None,
    production_paths: list[str] | None = None,
    allow_list: list[dict[str, str]] | None = None,
    patterns: list[re.Pattern[str]] | None = None,
) -> list[dict[str, Any]]:
    root = Path(workspace_root) if workspace_root else None
    if not root or not root.exists():
        return []

    pats = patterns or []
    allow_entries = [
        {
            "path": normalize_workspace_path(root, str(item.get("path") or "")),
            "pattern": str(item.get("pattern") or "").strip(),
        }
        for item in (allow_list or [])
        if isinstance(item, dict) and str(item.get("pattern") or "").strip()
    ]
    scopes = [normalize_workspace_path(root, item) for item in (production_paths or []) if str(item or "").strip()]

    if changed_files:
        files_to_scan = [root / f if not os.path.isabs(f) else Path(f) for f in changed_files]
    else:
        files_to_scan = []
        for ext in ("*.py", "*.php", "*.js", "*.ts", "*.java", "*.go", "*.rs"):
            files_to_scan.extend(root.rglob(ext))

    findings: list[dict[str, Any]] = []
    for fpath in files_to_scan:
        if not fpath.is_file():
            continue
        if any(part in _EXCLUDE_DIRS for part in fpath.parts):
            continue
        rel_path = normalize_workspace_path(root, str(fpath))
        if scopes and not any(rel_path == scope or rel_path.startswith(scope.rstrip("/") + "/") for scope in scopes):
            continue
        try:
            content = fpath.read_text(errors="replace")
        except OSError:
            continue
        for i, line in enumerate(content.split("\n"), 1):
            for pattern in pats:
                if not pattern.search(line):
                    continue
                pattern_str = pattern.pattern
                allowed = any(
                    entry["path"] == rel_path and entry["pattern"] == pattern_str
                    for entry in allow_entries
                )
                if not allowed:
                    findings.append(
                        {
                            "file_path": rel_path,
                            "line_number": i,
                            "pattern": pattern_str,
                            "line_content": line.strip()[:200],
                        }
                    )
                break
    return findings

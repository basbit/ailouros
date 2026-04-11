"""Простое файловое хранилище «паттернов» (ключ → текст) без эмбеддингов.

Включается: ``agent_config.swarm.pattern_memory`` = true или env ``SWARM_PATTERN_MEMORY=1``.

Путь к JSON:
- ``agent_config.swarm.pattern_memory_path`` (абсолютный или относительно cwd), или
- env ``SWARM_PATTERN_MEMORY_PATH``, или
- ``<cwd>/.swarm/pattern_memory.json``.

Поиск: по пересечению токенов (len>=3) в ключах/значениях + бонус за подстроку запроса.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _truthy(val: Any) -> bool:
    if val is True:
        return True
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return False


def pattern_memory_enabled(state: Mapping[str, Any]) -> bool:
    env_val = os.getenv("SWARM_PATTERN_MEMORY")
    if env_val is not None:
        return _truthy(env_val)
    agent_config = state.get("agent_config")
    if isinstance(agent_config, dict):
        swarm_config = agent_config.get("swarm")
        if isinstance(swarm_config, dict) and "pattern_memory" in swarm_config:
            return _truthy(swarm_config.get("pattern_memory"))
    return True  # enabled by default


def pattern_memory_path_for_state(state: Mapping[str, Any]) -> Path:
    agent_config = state.get("agent_config")
    path_str = ""
    if isinstance(agent_config, dict):
        swarm_config = agent_config.get("swarm")
        if isinstance(swarm_config, dict):
            path_str = str(swarm_config.get("pattern_memory_path") or "").strip()
    if not path_str:
        path_str = os.getenv("SWARM_PATTERN_MEMORY_PATH", "").strip()
    if path_str:
        memory_path = Path(path_str).expanduser()
        if not memory_path.is_absolute():
            memory_path = Path.cwd() / memory_path
        return memory_path.resolve()
    return (Path.cwd() / ".swarm" / "pattern_memory.json").resolve()


def _normalize_token(text: str) -> list[str]:
    text = text.lower()
    return [token for token in re.split(r"[^\w]+", text) if len(token) >= 3]


def _load_store(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "namespaces": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        import logging as _pm_logging
        _pm_logging.getLogger(__name__).debug("pattern_memory: failed to load %s: %s", path, exc)
        return {"version": 1, "namespaces": {}}
    if not isinstance(data, dict):
        return {"version": 1, "namespaces": {}}
    ns = data.get("namespaces")
    if not isinstance(ns, dict):
        data["namespaces"] = {}
    return data


def _save_store(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def search_patterns(
    state: Mapping[str, Any],
    query: str,
    *,
    namespace: str = "default",
    limit: int = 5,
) -> list[tuple[str, str, float]]:
    if not pattern_memory_enabled(state):
        return []
    path = pattern_memory_path_for_state(state)
    data = _load_store(path)
    ns_map = data.get("namespaces") or {}
    if not isinstance(ns_map, dict):
        return []
    bucket = ns_map.get(namespace) or ns_map.get("default") or {}
    if not isinstance(bucket, dict):
        return []
    q_tokens = set(_normalize_token(query))
    if not q_tokens:
        q_tokens = set(_normalize_token(query[:200]))
    scored: list[tuple[str, str, float]] = []
    for key, val in bucket.items():
        if not isinstance(key, str) or not isinstance(val, str):
            continue
        hay = key + "\n" + val
        tokens = set(_normalize_token(hay))
        inter = len(q_tokens & tokens)
        score = float(inter)
        ql = query.lower().strip()
        if ql and (ql in key.lower() or ql in val.lower()):
            score += 3.0
        if score > 0:
            scored.append((key, val, score))
    scored.sort(key=lambda item: -item[2])
    return scored[: max(1, min(20, limit))]


def format_pattern_memory_block(
    state: Mapping[str, Any],
    query: str,
    *,
    namespace: str = "default",
    limit: int = 4,
    max_chars: int = 6000,
) -> str:
    hits = search_patterns(state, query, namespace=namespace, limit=limit)
    if not hits:
        return ""
    lines = [
        "[Память паттернов — похожие записи; подсказка, не ТЗ]\n"
    ]
    total = 0
    for pattern_key, pattern_value, score in hits:
        chunk = f"### {pattern_key} (score={score:.1f})\n{pattern_value.strip()}\n\n"
        if total + len(chunk) > max_chars:
            break
        lines.append(chunk)
        total += len(chunk)
    return "".join(lines).strip() + "\n\n"


def store_pattern(
    path: Path,
    namespace: str,
    key: str,
    value: str,
    *,
    merge: bool = True,
) -> None:
    key = key.strip()
    if not key or not value.strip():
        raise ValueError("key and value required")
    data = _load_store(path)
    ns_map = data.setdefault("namespaces", {})
    assert isinstance(ns_map, dict)
    bucket = ns_map.setdefault(namespace, {})
    assert isinstance(bucket, dict)
    if merge and isinstance(bucket.get(key), str) and bucket[key].strip():
        bucket[key] = bucket[key].rstrip() + "\n\n---\n\n" + value.strip()
    else:
        bucket[key] = value.strip()
    _save_store(path, data)


def store_consolidated_pattern(
    pattern_key: str,
    value: str,
    provenance_ids: list[str],
    path: Path | None = None,
) -> None:
    """Store a pattern produced by memory consolidation (K-7 Dream pass).

    Writes into the ``consolidated`` namespace and appends provenance metadata
    so that the origin episodes can be traced back. Provenance is embedded as
    a footer line in the stored value.

    Args:
        pattern_key: Unique key for the pattern (e.g. ``"dream:pm:dev"``).
        value: Pattern text to store.
        provenance_ids: List of source episode IDs (task_id or step labels).
        path: Path to the pattern_memory JSON file. Defaults to
              ``<cwd>/.swarm/pattern_memory.json``.
    """
    if path is None:
        path = (Path.cwd() / ".swarm" / "pattern_memory.json").resolve()
    key = pattern_key.strip()
    if not key or not value.strip():
        raise ValueError("pattern_key and value required")
    provenance_str = ", ".join(str(pid) for pid in provenance_ids if str(pid).strip())
    full_value = value.strip()
    if provenance_str:
        full_value += f"\n\n[provenance: {provenance_str}]"
    store_pattern(path, "consolidated", key, full_value, merge=False)

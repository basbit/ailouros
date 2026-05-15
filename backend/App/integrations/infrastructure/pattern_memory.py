from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from backend.App.shared.domain.validators import is_truthy_value
from backend.App.shared.infrastructure.env_flags import is_truthy_env
from backend.App.shared.infrastructure.json_file_io import read_json_file, write_json_file

logger = logging.getLogger(__name__)


def _semantic_enabled() -> bool:
    return is_truthy_env("SWARM_PATTERN_MEMORY_SEMANTIC", default=True)


def _semantic_weight() -> float:
    raw = os.getenv("SWARM_PATTERN_MEMORY_SEMANTIC_WEIGHT", "0.7")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.7
    return max(0.0, min(1.0, value))


def pattern_memory_enabled(state: Mapping[str, Any]) -> bool:
    env_val = os.getenv("SWARM_PATTERN_MEMORY")
    if env_val is not None:
        return is_truthy_value(env_val)
    agent_config = state.get("agent_config")
    if isinstance(agent_config, dict):
        swarm_config = agent_config.get("swarm")
        if isinstance(swarm_config, dict) and "pattern_memory" in swarm_config:
            return is_truthy_value(swarm_config.get("pattern_memory"))
    return True


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


from backend.App.shared.application.text_tokenize import tokenize_for_search as _normalize_token  # noqa: E402


def _token_score(query: str, hay: str) -> float:
    q_tokens = set(_normalize_token(query))
    if not q_tokens:
        q_tokens = set(_normalize_token(query[:200]))
    if not q_tokens:
        return 0.0
    body_tokens = set(_normalize_token(hay))
    inter = len(q_tokens & body_tokens)
    score = float(inter)
    ql = query.lower().strip()
    if ql and ql in hay.lower():
        score += 3.0
    return score


from backend.App.shared.domain.vector_math import cosine_dense as _cosine  # noqa: E402


def _get_provider() -> Optional[Any]:
    if not _semantic_enabled():
        return None
    try:
        from backend.App.integrations.infrastructure.embedding_service import (
            get_embedding_provider,
        )
    except ImportError:
        return None
    provider = get_embedding_provider()
    if getattr(provider, "name", "") == "null":
        return None
    return provider


def _embed_pattern(provider: Any, key: str, value: str) -> list[float]:
    text = f"{key}\n{value}".strip()
    if not text:
        return []
    from backend.App.integrations.infrastructure.embedding_service import EmbeddingError
    try:
        vectors = provider.embed([text])
    except EmbeddingError as exc:
        logger.warning("pattern_memory: embed failed (%s); storing without vector", exc)
        return []
    return list(vectors[0]) if vectors else []


def _embed_query(provider: Any, query: str) -> list[float]:
    if not query.strip():
        return []
    from backend.App.integrations.infrastructure.embedding_service import EmbeddingError
    try:
        vectors = provider.embed([query])
    except EmbeddingError as exc:
        logger.warning("pattern_memory: query embed failed (%s)", exc)
        return []
    return list(vectors[0]) if vectors else []


def _load_store(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 2, "namespaces": {}, "vectors": {}}
    try:
        data = read_json_file(path)
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("pattern_memory: failed to load %s: %s", path, exc)
        return {"version": 2, "namespaces": {}, "vectors": {}}
    if not isinstance(data, dict):
        return {"version": 2, "namespaces": {}, "vectors": {}}
    ns = data.get("namespaces")
    if not isinstance(ns, dict):
        data["namespaces"] = {}
    vectors_block = data.get("vectors")
    if not isinstance(vectors_block, dict):
        data["vectors"] = {}
    return data


def _save_store(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_file(path, data)


def _vector_bucket(data: dict[str, Any], namespace: str) -> dict[str, list[float]]:
    vectors_block = data.setdefault("vectors", {})
    assert isinstance(vectors_block, dict)
    bucket = vectors_block.setdefault(namespace, {})
    assert isinstance(bucket, dict)
    return bucket  # type: ignore[return-value]


def search_patterns(
    state: Mapping[str, Any],
    query: str,
    *,
    namespace: str = "default",
    limit: int = 5,
    current_spec_id: str = "",
    current_spec_hash: str = "",
) -> list[tuple[str, str, float]]:
    if not pattern_memory_enabled(state):
        return []
    path = pattern_memory_path_for_state(state)
    data = _load_store(path)
    ns_map = data.get("namespaces") or {}
    if not isinstance(ns_map, dict):
        return []
    bucket_namespace = namespace if namespace in ns_map else "default"
    bucket = ns_map.get(bucket_namespace) or {}
    if not isinstance(bucket, dict) or not bucket:
        return []

    provider = _get_provider()
    query_vec: list[float] = _embed_query(provider, query) if provider else []
    semantic_weight = _semantic_weight() if query_vec else 0.0
    vectors_bucket = data.get("vectors", {}).get(namespace, {}) if isinstance(data.get("vectors"), dict) else {}
    if namespace not in (data.get("vectors") or {}):
        vectors_bucket = data.get("vectors", {}).get("default", {}) or {}

    scored: list[tuple[str, str, float]] = []
    for key, val in bucket.items():
        if not isinstance(key, str) or not isinstance(val, str):
            continue
        provenance = _provenance_for(data, bucket_namespace, key)
        if _is_quarantined(
            provenance,
            current_spec_id=current_spec_id,
            current_spec_hash=current_spec_hash,
        ):
            continue
        token_part = _token_score(query, key + "\n" + val)
        cosine_part = 0.0
        if query_vec:
            stored_vec = vectors_bucket.get(key) if isinstance(vectors_bucket, dict) else None
            if isinstance(stored_vec, list) and len(stored_vec) == len(query_vec):
                cosine_part = max(0.0, _cosine(query_vec, [float(x) for x in stored_vec]))
        hybrid = (semantic_weight * cosine_part * 5.0) + (1.0 - semantic_weight) * token_part
        if hybrid > 0:
            scored.append((key, val, hybrid))
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
    if max_chars <= 0:
        return ""
    hits = search_patterns(state, query, namespace=namespace, limit=limit)
    if not hits:
        return ""
    lines = [
        "[Pattern memory — similar entries; context hint, not a requirement]\n"
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
    agent: str = "",
    spec_id: str = "",
    spec_hash: str = "",
) -> None:
    import time as _time

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

    provenance_block = data.setdefault("provenance", {})
    assert isinstance(provenance_block, dict)
    provenance_ns = provenance_block.setdefault(namespace, {})
    assert isinstance(provenance_ns, dict)
    provenance_ns[key] = {
        "agent": str(agent),
        "spec_id": str(spec_id),
        "spec_hash": str(spec_hash),
        "recorded_at": _time.time(),
    }

    _save_store(path, data)
    provider = _get_provider()
    if provider is None:
        return
    vec = _embed_pattern(provider, key, bucket[key])
    if vec:
        vector_bucket = _vector_bucket(data, namespace)
        vector_bucket[key] = vec
        data["version"] = 2
        _save_store(path, data)


def _provenance_for(data: dict[str, Any], namespace: str, key: str) -> dict[str, Any]:
    provenance_block = data.get("provenance") or {}
    if not isinstance(provenance_block, dict):
        return {}
    ns_block = provenance_block.get(namespace) or {}
    if not isinstance(ns_block, dict):
        return {}
    entry = ns_block.get(key) or {}
    return entry if isinstance(entry, dict) else {}


def _is_quarantined(
    entry_provenance: dict[str, Any],
    *,
    current_spec_id: str,
    current_spec_hash: str,
) -> bool:
    if not current_spec_id or not current_spec_hash:
        return False
    recorded_spec_id = str(entry_provenance.get("spec_id") or "")
    if not recorded_spec_id or recorded_spec_id != current_spec_id:
        return False
    recorded_hash = str(entry_provenance.get("spec_hash") or "")
    if not recorded_hash:
        return False
    return recorded_hash != current_spec_hash


def list_quarantined_patterns(
    path: Path,
    *,
    current_spec_id: str,
    current_spec_hash: str,
) -> list[dict[str, Any]]:
    if not current_spec_id or not current_spec_hash:
        return []
    if not path.is_file():
        return []
    data = _load_store(path)
    quarantined: list[dict[str, Any]] = []
    provenance_block = data.get("provenance") or {}
    if not isinstance(provenance_block, dict):
        return []
    namespaces_block = data.get("namespaces") or {}
    if not isinstance(namespaces_block, dict):
        return []
    for namespace, entries in provenance_block.items():
        if not isinstance(entries, dict):
            continue
        ns_bucket = namespaces_block.get(namespace) or {}
        for key, prov in entries.items():
            if not isinstance(prov, dict):
                continue
            if _is_quarantined(
                prov,
                current_spec_id=current_spec_id,
                current_spec_hash=current_spec_hash,
            ):
                quarantined.append(
                    {
                        "namespace": namespace,
                        "key": key,
                        "value": ns_bucket.get(key, "") if isinstance(ns_bucket, dict) else "",
                        "provenance": dict(prov),
                    }
                )
    return quarantined


def store_consolidated_pattern(
    pattern_key: str,
    value: str,
    provenance_ids: list[str],
    path: Path | None = None,
) -> None:
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

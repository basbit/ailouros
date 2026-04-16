"""Pattern memory — file-backed key→text store with optional semantic search.

Toggles:
- ``agent_config.swarm.pattern_memory`` = ``true`` or ``SWARM_PATTERN_MEMORY=1``
  to enable the layer at all (default: enabled).
- ``SWARM_PATTERN_MEMORY_SEMANTIC=1`` (default) to add embedding-based
  scoring on top of the legacy token-overlap signal. When the embedding
  provider is null or returns empty vectors, semantic ranking is skipped
  and only the legacy token score is used — no behaviour silently
  disappears.

Storage path:
- ``agent_config.swarm.pattern_memory_path`` (absolute or cwd-relative), or
- ``SWARM_PATTERN_MEMORY_PATH``, or
- ``<cwd>/.swarm/pattern_memory.json``.

JSON layout (backwards compatible — readers that don't know about
``vectors`` simply ignore it):

.. code-block:: json

    {
      "version": 2,
      "namespaces": {"default": {"key": "text body"}},
      "vectors":    {"default": {"key": [0.12, 0.04, …]}}
    }

Vectors are stored alongside their entries. When the embedding provider
changes (different model / dim) at read time, mismatched vectors are
ignored and the search degrades to token-overlap until the entry is
re-stored.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------


def _truthy(val: Any) -> bool:
    if val is True:
        return True
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return False


def _semantic_enabled() -> bool:
    raw = os.getenv("SWARM_PATTERN_MEMORY_SEMANTIC")
    if raw is None:
        return True
    return _truthy(raw)


def _semantic_weight() -> float:
    """Weight α for the cosine score in the hybrid ranking ``α·cos + (1-α)·tok``."""
    raw = os.getenv("SWARM_PATTERN_MEMORY_SEMANTIC_WEIGHT", "0.7")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.7
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Enabling / paths
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Token-overlap helpers (legacy fallback)
# ---------------------------------------------------------------------------


def _normalize_token(text: str) -> list[str]:
    text = text.lower()
    return [token for token in re.split(r"[^\w]+", text) if len(token) >= 3]


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


# ---------------------------------------------------------------------------
# Cosine
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------


def _get_provider() -> Optional[Any]:
    """Return the configured embedding provider, or ``None`` when it should not be used.

    Imports lazily so ``pattern_memory`` keeps working even if the
    embedding sub-tree is removed in a stripped-down deployment.
    """
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
    """Embed ``"{key}\n{value}"``; returns an empty list on failure."""
    text = f"{key}\n{value}".strip()
    if not text:
        return []
    try:
        vectors = provider.embed([text])
    except Exception as exc:
        logger.warning("pattern_memory: embed failed (%s); storing without vector", exc)
        return []
    return list(vectors[0]) if vectors else []


def _embed_query(provider: Any, query: str) -> list[float]:
    if not query.strip():
        return []
    try:
        vectors = provider.embed([query])
    except Exception as exc:
        logger.warning("pattern_memory: query embed failed (%s)", exc)
        return []
    return list(vectors[0]) if vectors else []


# ---------------------------------------------------------------------------
# Storage IO
# ---------------------------------------------------------------------------


def _load_store(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 2, "namespaces": {}, "vectors": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _vector_bucket(data: dict[str, Any], namespace: str) -> dict[str, list[float]]:
    vectors_block = data.setdefault("vectors", {})
    assert isinstance(vectors_block, dict)
    bucket = vectors_block.setdefault(namespace, {})
    assert isinstance(bucket, dict)
    return bucket  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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
    if not isinstance(bucket, dict) or not bucket:
        return []

    provider = _get_provider()
    query_vec: list[float] = _embed_query(provider, query) if provider else []
    semantic_weight = _semantic_weight() if query_vec else 0.0
    vectors_bucket = data.get("vectors", {}).get(namespace, {}) if isinstance(data.get("vectors"), dict) else {}
    if namespace not in (data.get("vectors") or {}):
        # Fall through to default vectors when the requested ns has no
        # entries but the default does (mirrors the bucket fallback above).
        vectors_bucket = data.get("vectors", {}).get("default", {}) or {}

    scored: list[tuple[str, str, float]] = []
    for key, val in bucket.items():
        if not isinstance(key, str) or not isinstance(val, str):
            continue
        token_part = _token_score(query, key + "\n" + val)
        cosine_part = 0.0
        if query_vec:
            stored_vec = vectors_bucket.get(key) if isinstance(vectors_bucket, dict) else None
            if isinstance(stored_vec, list) and len(stored_vec) == len(query_vec):
                cosine_part = max(0.0, _cosine(query_vec, [float(x) for x in stored_vec]))
        # Hybrid score in the same magnitude family as the legacy token
        # score. Cosine ∈ [0, 1] is multiplied up by 5 so a strong semantic
        # match can outweigh one or two stray token overlaps.
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
    """Render the pattern-memory block.

    ``max_chars <= 0`` is treated as "block disabled" — used by per-step
    :class:`ContextBudget` callers that want to skip pattern memory
    entirely (e.g. QA / Dev with ``pattern_memory_chars=0``). Skipping
    the search call avoids touching the JSON store on the disabled
    paths.
    """
    if max_chars <= 0:
        return ""
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

    # Refresh the embedding for the (possibly merged) value. Done after
    # the JSON write to ensure provider failures never lose the textual
    # entry — the worst case is an entry without a vector, which the
    # search code handles gracefully.
    _save_store(path, data)
    provider = _get_provider()
    if provider is None:
        return
    vec = _embed_pattern(provider, key, bucket[key])
    if vec:
        vector_bucket = _vector_bucket(data, namespace)
        vector_bucket[key] = vec
        # Bump version once we attach vectors; harmless for legacy readers.
        data["version"] = 2
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

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Mapping
from typing import Any, Optional

from backend.App.shared.domain.validators import is_truthy_value
from backend.App.shared.infrastructure.env_flags import is_truthy_env

logger = logging.getLogger(__name__)

_DEFAULT_PERSIST_STEPS: frozenset[str] = frozenset(
    os.getenv("SWARM_CROSS_TASK_PERSIST_STEPS", "pm,ba,architect,spec_merge").split(",")
)
_DEFAULT_INJECT_AT_STEPS: frozenset[str] = frozenset(
    os.getenv("SWARM_CROSS_TASK_INJECT_STEPS", "pm,ba,architect,clarify_input").split(",")
)

try:
    import redis as _redis_module
except ImportError:
    _redis_module = None  # type: ignore[assignment]
redis = _redis_module

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_MEM_LIST_MAX = 400
_LOCAL_EPISODES: dict[str, list[dict[str, Any]]] = {}
_redis_client: Optional[Any] = None
_STRUCTURED_MEMORY_KEYS: tuple[str, ...] = (
    "facts",
    "hypotheses",
    "decisions",
    "dead_ends",
    "constraints",
)
_MEMORY_ARTIFACT_FACT_KEYS: tuple[str, ...] = ("verified_facts", "facts")
_MEMORY_ARTIFACT_STATE_KEYS: dict[str, str] = {
    "pm": "pm_memory_artifact",
    "ba": "ba_memory_artifact",
    "architect": "arch_memory_artifact",
    "spec_merge": "spec_memory_artifact",
}
_MAX_MEMORY_ITEM_CHARS = 220
_GENERIC_MEMORY_PHRASES: tuple[str, ...] = (
    "according to the specification",
    "according to spec",
    "per spec",
    "follow the specification",
    "follow the approved specification",
    "implement the remaining scope",
    "as described in the specification",
    "as described above",
)


def _swarm_block(state: Mapping[str, Any]) -> dict[str, Any]:
    agent_config = state.get("agent_config")
    if not isinstance(agent_config, dict):
        return {}
    swarm_config = agent_config.get("swarm")
    return swarm_config if isinstance(swarm_config, dict) else {}


def _memory_configuration(state: Mapping[str, Any]) -> dict[str, Any]:
    swarm = _swarm_block(state)
    raw = swarm.get("cross_task_memory")
    return raw if isinstance(raw, dict) else {}


def cross_task_memory_enabled(state: Mapping[str, Any]) -> bool:
    env_value = os.getenv("SWARM_CROSS_TASK_MEMORY")
    if env_value is not None:
        return is_truthy_value(env_value)
    configuration_enabled = _memory_configuration(state).get("enabled")
    if configuration_enabled is not None:
        return is_truthy_value(configuration_enabled)
    return True


def memory_namespace(state: Mapping[str, Any]) -> str:
    configuration = _memory_configuration(state)
    explicit_namespace = str(configuration.get("namespace") or "").strip()
    if explicit_namespace:
        return explicit_namespace[:128]
    workspace_root = str(state.get("workspace_root") or "").strip()
    if workspace_root:
        workspace_hash = hashlib.sha256(workspace_root.encode("utf-8")).hexdigest()[:20]
        return f"ws:{workspace_hash}"
    return "default"


def _redis() -> Optional[Any]:
    global _redis_client
    if redis is None:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        _redis_client = redis.Redis.from_url(_REDIS_URL, decode_responses=True)
        _redis_client.ping()
    except Exception as exc:
        logger.debug("cross_task_memory: redis unavailable (%s)", exc)
        _redis_client = False
    return _redis_client if _redis_client else None


def _list_key(namespace: str) -> str:
    return f"swarm:xmem:{namespace}"


def _max_items(state: Mapping[str, Any]) -> int:
    configuration = _memory_configuration(state)
    try:
        max_count = int(configuration.get("max_list_items") or _MEM_LIST_MAX)
    except (TypeError, ValueError):
        max_count = _MEM_LIST_MAX
    return max(10, min(5000, max_count))


def _normalize_memory_items(raw: Any) -> list[str]:
    items: list[str] = []
    for item in list(raw or []):
        text = str(item or "").strip()
        if text and text not in items:
            items.append(text)
    return items


def _sanitize_memory_items(raw: Any, *, category: str) -> list[str]:
    items: list[str] = []
    for text in _normalize_memory_items(raw):
        lowered = text.lower()
        if len(text) > _MAX_MEMORY_ITEM_CHARS:
            continue
        if category in {"facts", "decisions", "verified_facts"} and any(
            phrase in lowered for phrase in _GENERIC_MEMORY_PHRASES
        ):
            continue
        items.append(text)
    return items


def normalize_memory_artifact(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    facts: list[str] = []
    for key in _MEMORY_ARTIFACT_FACT_KEYS:
        for item in _sanitize_memory_items(raw.get(key), category=key):
            if item not in facts:
                facts.append(item)
    normalized: dict[str, Any] = {
        "facts": facts,
        "hypotheses": _sanitize_memory_items(raw.get("hypotheses"), category="hypotheses"),
        "decisions": _sanitize_memory_items(raw.get("decisions"), category="decisions"),
        "dead_ends": _sanitize_memory_items(raw.get("dead_ends"), category="dead_ends"),
        "constraints": _sanitize_memory_items(raw.get("constraints"), category="constraints"),
    }
    if any(normalized.values()):
        normalized["structured"] = True
        normalized["facts_are_verified"] = bool(_sanitize_memory_items(raw.get("verified_facts"), category="verified_facts"))
        return normalized
    return {}


def memory_artifact_state_key(step_id: str) -> str:
    explicit_key = _MEMORY_ARTIFACT_STATE_KEYS.get(step_id, "")
    if explicit_key:
        return explicit_key
    if step_id.startswith("crole_"):
        return f"{step_id}_memory_artifact"
    return ""


def _parse_structured_memory_body(body: str) -> dict[str, Any]:
    text = (body or "").strip()
    default = {
        "facts": [text] if text else [],
        "hypotheses": [],
        "decisions": [],
        "dead_ends": [],
        "constraints": [],
        "structured": False,
    }
    if not text:
        return default
    blocks = re.findall(r"```(?:json)?\s*({[\s\S]*?})\s*```", text, flags=re.IGNORECASE)
    candidates = blocks + [text]
    for candidate in candidates:
        try:
            data = json.loads(candidate.strip())
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        normalized: dict[str, Any] = {
            key: _normalize_memory_items(data.get(key))
            for key in _STRUCTURED_MEMORY_KEYS
        }
        if any(normalized.values()):
            normalized["structured"] = True
            return normalized
    return default


def _render_structured_memory(structured: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in _STRUCTURED_MEMORY_KEYS:
        items = [str(item).strip() for item in list(structured.get(key) or []) if str(item).strip()]
        if not items:
            continue
        title = key.replace("_", " ").title()
        parts.append(f"## {title}")
        for item in items:
            parts.append(f"- {item}")
        parts.append("")
    return "\n".join(parts).strip()


def _semantic_enabled() -> bool:
    return is_truthy_env("SWARM_CROSS_TASK_MEMORY_SEMANTIC", default=True)


def _semantic_weight() -> float:
    raw = os.getenv("SWARM_CROSS_TASK_MEMORY_SEMANTIC_WEIGHT", "0.7")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.7
    return max(0.0, min(1.0, value))


def _get_embedding_provider() -> Optional[Any]:
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


def _embed_episode_body(provider: Any, step_id: str, body: str) -> list[float]:
    text = f"{step_id}\n{body}".strip()
    if not text:
        return []
    from backend.App.integrations.infrastructure.embedding_service import EmbeddingError
    try:
        vectors = provider.embed([text[:4000]])
    except EmbeddingError as exc:
        logger.warning("cross_task_memory: embed failed (%s); episode stored without vector", exc)
        return []
    return list(vectors[0]) if vectors else []


def _embed_query(provider: Any, query: str) -> list[float]:
    if not query.strip():
        return []
    from backend.App.integrations.infrastructure.embedding_service import EmbeddingError
    try:
        vectors = provider.embed([query[:4000]])
    except EmbeddingError as exc:
        logger.warning("cross_task_memory: query embed failed (%s)", exc)
        return []
    return list(vectors[0]) if vectors else []


from backend.App.shared.domain.vector_math import cosine_dense as _cosine  # noqa: E402


def _build_episode_payload(
    *,
    step_id: str,
    body: str,
    task_id: str,
    artifact: Mapping[str, Any] | None = None,
    agent: str = "",
    spec_id: str = "",
    spec_hash: str = "",
) -> dict[str, Any]:
    structured = normalize_memory_artifact(artifact)
    if not structured:
        structured = _parse_structured_memory_body(body)
    rendered_body = _render_structured_memory(structured)
    if not rendered_body:
        rendered_body = (body or "").strip()
    payload: dict[str, Any] = {
        "ts": time.time(),
        "step": step_id,
        "task_id": (task_id or "").strip(),
        "body": rendered_body[:16000],
        "facts": list(structured.get("facts") or []),
        "facts_are_verified": bool(structured.get("facts_are_verified")),
        "hypotheses": list(structured.get("hypotheses") or []),
        "decisions": list(structured.get("decisions") or []),
        "dead_ends": list(structured.get("dead_ends") or []),
        "constraints": list(structured.get("constraints") or []),
        "structured": bool(structured.get("structured")),
        "_provenance": {
            "agent": str(agent),
            "spec_id": str(spec_id),
            "spec_hash": str(spec_hash),
            "recorded_at": time.time(),
        },
    }
    provider = _get_embedding_provider()
    if provider is not None:
        embedding = _embed_episode_body(provider, step_id, payload["body"])
        if embedding:
            payload["embedding"] = embedding
    return payload


def append_episode(
    state: Mapping[str, Any],
    *,
    step_id: str,
    body: str,
    task_id: str = "",
    artifact: Mapping[str, Any] | None = None,
    agent: str = "",
    spec_id: str = "",
    spec_hash: str = "",
) -> None:
    if not cross_task_memory_enabled(state):
        return
    text = (body or "").strip()
    if not text and not normalize_memory_artifact(artifact):
        return
    namespace = memory_namespace(state)
    max_items = _max_items(state)
    episode = _build_episode_payload(
        step_id=step_id,
        body=text,
        task_id=task_id,
        artifact=artifact,
        agent=agent,
        spec_id=spec_id,
        spec_hash=spec_hash,
    )
    payload = json.dumps(episode, ensure_ascii=False)
    redis_client = _redis()
    if redis_client:
        key = _list_key(namespace)
        redis_client.lpush(key, payload)
        redis_client.ltrim(key, 0, max_items - 1)
        return
    bucket = _LOCAL_EPISODES.setdefault(namespace, [])
    bucket.insert(0, episode)
    del bucket[max_items:]


def _episode_is_quarantined(
    episode: Mapping[str, Any],
    *,
    current_spec_id: str,
    current_spec_hash: str,
) -> bool:
    if not current_spec_id or not current_spec_hash:
        return False
    provenance = episode.get("_provenance") or {}
    if not isinstance(provenance, dict):
        return False
    recorded_spec_id = str(provenance.get("spec_id") or "")
    if not recorded_spec_id or recorded_spec_id != current_spec_id:
        return False
    recorded_hash = str(provenance.get("spec_hash") or "")
    if not recorded_hash:
        return False
    return recorded_hash != current_spec_hash


def list_quarantined_episodes(
    state: Mapping[str, Any],
    *,
    current_spec_id: str,
    current_spec_hash: str,
) -> list[dict[str, Any]]:
    if not current_spec_id or not current_spec_hash:
        return []
    if not cross_task_memory_enabled(state):
        return []
    namespace = memory_namespace(state)
    redis_client = _redis()
    raw_entries: list[str] = []
    if redis_client:
        key = _list_key(namespace)
        raw_entries = [
            payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else str(payload)
            for payload in redis_client.lrange(key, 0, -1)
        ]
    else:
        for episode in _LOCAL_EPISODES.get(namespace, []):
            raw_entries.append(json.dumps(episode, ensure_ascii=False))

    quarantined: list[dict[str, Any]] = []
    for entry in raw_entries:
        try:
            parsed = json.loads(entry)
        except (TypeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue
        if _episode_is_quarantined(
            parsed,
            current_spec_id=current_spec_id,
            current_spec_hash=current_spec_hash,
        ):
            quarantined.append(parsed)
    return quarantined


def persist_after_pipeline_step(
    step_id: str,
    state: Mapping[str, Any],
    step_delta: Mapping[str, Any],
) -> None:
    if not cross_task_memory_enabled(state):
        return
    configuration = _memory_configuration(state)
    steps = configuration.get("persist_steps")
    allowed: set[str] | frozenset[str]
    if isinstance(steps, list):
        allowed = {str(step).strip() for step in steps if str(step).strip()}
    else:
        allowed = _DEFAULT_PERSIST_STEPS
    if step_id not in allowed:
        return

    from backend.App.orchestration.application.routing.pipeline_graph import ARTIFACT_AGENT_OUTPUT_KEYS

    output_key: Optional[str] = None
    for artifact_step_id, key in ARTIFACT_AGENT_OUTPUT_KEYS:
        if artifact_step_id == step_id:
            output_key = key
            break
    if not output_key and step_id.startswith("crole_"):
        output_key = f"{step_id}_output"
    if not output_key:
        return
    artifact_key = memory_artifact_state_key(step_id)
    artifact = normalize_memory_artifact(step_delta.get(artifact_key))
    if not artifact and artifact_key:
        artifact = normalize_memory_artifact(state.get(artifact_key))
    body = step_delta.get(output_key)
    if body is None:
        body = state.get(output_key) if isinstance(state, Mapping) else None
    if artifact_key and not artifact:
        logger.debug(
            "cross_task_memory: skipping %s persistence because canonical %s is missing",
            step_id,
            artifact_key,
        )
        return
    if not isinstance(body, str) or not body.strip():
        if not artifact:
            return
        body = _render_structured_memory(artifact)
    if not isinstance(body, str) or not body.strip():
        return
    task_id = str(state.get("task_id") or "") if isinstance(state, Mapping) else ""
    append_episode(state, step_id=step_id, body=body, task_id=task_id, artifact=artifact or None)


from backend.App.shared.application.text_tokenize import tokenize_for_search as _normalize_token  # noqa: E402


def _load_episodes(namespace: str, *, limit: int) -> list[dict[str, Any]]:
    redis_client = _redis()
    episodes: list[dict[str, Any]] = []
    if redis_client:
        key = _list_key(namespace)
        serialized_items = redis_client.lrange(key, 0, max(0, limit * 3))
        for serialized in serialized_items:
            try:
                episode = json.loads(serialized)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(episode, dict) and episode.get("body"):
                episodes.append(episode)
            if len(episodes) >= limit:
                break
        return episodes
    for episode in _LOCAL_EPISODES.get(namespace, [])[:limit]:
        if isinstance(episode, dict) and episode.get("body"):
            episodes.append(episode)
    return episodes


def _token_relevance(query: str, body: str) -> float:
    query_tokens = set(_normalize_token(query))
    if not query_tokens:
        query_tokens = set(_normalize_token(query[:200]))
    tokens = set(_normalize_token(body))
    intersection = len(query_tokens & tokens)
    score = float(intersection)
    query_lower = query.lower().strip()
    body_lower = body.lower()
    if query_lower and query_lower in body_lower:
        score += 4.0
    return score


def _score_episode(query: str, body: str) -> float:
    return _token_relevance(query, body)


def search_episodes(
    state: Mapping[str, Any],
    query: str,
    *,
    limit: int = 8,
    current_spec_id: str = "",
    current_spec_hash: str = "",
) -> list[tuple[dict[str, Any], float]]:
    if not cross_task_memory_enabled(state):
        return []
    configuration = _memory_configuration(state)
    namespace = memory_namespace(state)
    pool_size = int(configuration.get("retrieve_pool") or 80)
    pool_size = max(20, min(500, pool_size))
    episodes = _load_episodes(namespace, limit=pool_size)

    provider = _get_embedding_provider()
    query_vector: list[float] = _embed_query(provider, query) if provider else []
    semantic_weight = _semantic_weight() if query_vector else 0.0

    scored: list[tuple[dict[str, Any], float]] = []
    for episode in episodes:
        if _episode_is_quarantined(
            episode,
            current_spec_id=current_spec_id,
            current_spec_hash=current_spec_hash,
        ):
            continue
        body = str(episode.get("body") or "")
        token_part = _token_relevance(query, body)
        cosine_part = 0.0
        if query_vector:
            stored_vector = episode.get("embedding")
            if isinstance(stored_vector, list) and len(stored_vector) == len(query_vector):
                cosine_part = max(0.0, _cosine(query_vector, [float(x) for x in stored_vector]))
        hybrid = (semantic_weight * cosine_part * 5.0) + (1.0 - semantic_weight) * token_part
        if hybrid > 0:
            scored.append((episode, hybrid))
    scored.sort(key=lambda item: -item[1])
    lim = max(1, min(30, limit))
    return scored[:lim]


def should_inject_at_step(state: Mapping[str, Any], step_id: str) -> bool:
    if not cross_task_memory_enabled(state):
        return False
    configuration = _memory_configuration(state)
    raw = configuration.get("inject_at_steps")
    if isinstance(raw, list) and raw:
        return step_id in {str(step).strip() for step in raw if str(step).strip()}
    return step_id in _DEFAULT_INJECT_AT_STEPS


def format_cross_task_memory_block(
    state: Mapping[str, Any],
    query: str,
    *,
    current_step: str = "pm",
    max_chars: Optional[int] = None,
) -> str:
    if not should_inject_at_step(state, current_step):
        return ""
    configuration = _memory_configuration(state)
    try:
        retrieve_limit = int(configuration.get("retrieve_limit") or 6)
    except (TypeError, ValueError):
        retrieve_limit = 6
    if max_chars is None:
        try:
            max_chars = int(configuration.get("max_inject_chars") or 8000)
        except (TypeError, ValueError):
            max_chars = 8000
    if max_chars <= 0:
        return ""
    hits = search_episodes(state, query, limit=retrieve_limit)
    if not hits:
        return ""
    lines = [
        "[Cross-task memory — compressed episodes from previous runs in this namespace; context hint, not a requirement]\n"
    ]
    total = 0
    for episode, relevance_score in hits:
        step_name = str(episode.get("step") or "?")
        task_id_prefix = str(episode.get("task_id") or "")
        episode_body = _render_structured_memory(episode).strip() or str(episode.get("body") or "").strip()
        episode_header = f"### step={step_name} task={task_id_prefix[:8]}… score={relevance_score:.1f}\n"
        episode_chunk = episode_header + episode_body + "\n\n"
        if total + len(episode_chunk) > max_chars:
            break
        lines.append(episode_chunk)
        total += len(episode_chunk)
    return "".join(lines).strip() + "\n\n"

"""Cross-task memory (episodes): Redis or in-process memory, token-overlap search.

Enable via: ``SWARM_CROSS_TASK_MEMORY=1`` or ``agent_config.swarm.cross_task_memory.enabled``.

Policy (``agent_config.swarm.cross_task_memory``):

- ``namespace`` — key; falls back to SHA256 of ``workspace_root``, then ``default``.
- ``persist_steps`` — steps after which an episode is written (e.g. ``human_spec``, ``human_qa``).
- ``inject_at_steps`` — steps at which the memory block is injected into context (default: ``pm`` only).
- ``max_list_items`` — maximum episodes to keep in Redis (LPUSH+LTRIM).
- ``retrieve_limit``, ``max_inject_chars`` — search pool size and block size limit for PM (and BA on inject).

Search vector: no heavy dependencies — same token-intersection scoring as pattern_memory.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Mapping
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PERSIST_STEPS: frozenset[str] = frozenset(
    os.getenv("SWARM_CROSS_TASK_PERSIST_STEPS", "pm,ba,architect,spec_merge").split(",")
)
_DEFAULT_INJECT_AT_STEPS: frozenset[str] = frozenset(
    os.getenv("SWARM_CROSS_TASK_INJECT_STEPS", "pm,ba,architect,clarify_input").split(",")
)

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_MEM_LIST_MAX = 400
_LOCAL_EPISODES: dict[str, list[dict[str, Any]]] = {}
_redis_client: Optional[Any] = None  # Union[redis.Redis, bool, None]
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


def _truthy(val: Any) -> bool:
    if val is True:
        return True
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return False


def _swarm_block(state: Mapping[str, Any]) -> dict[str, Any]:
    agent_config = state.get("agent_config")
    if not isinstance(agent_config, dict):
        return {}
    swarm_config = agent_config.get("swarm")
    return swarm_config if isinstance(swarm_config, dict) else {}


def _mem_cfg(state: Mapping[str, Any]) -> dict[str, Any]:
    sw = _swarm_block(state)
    raw = sw.get("cross_task_memory")
    return raw if isinstance(raw, dict) else {}


def cross_task_memory_enabled(state: Mapping[str, Any]) -> bool:
    env_val = os.getenv("SWARM_CROSS_TASK_MEMORY")
    if env_val is not None:
        return _truthy(env_val)
    cfg_enabled = _mem_cfg(state).get("enabled")
    if cfg_enabled is not None:
        return _truthy(cfg_enabled)
    return True  # enabled by default


def memory_namespace(state: Mapping[str, Any]) -> str:
    cfg = _mem_cfg(state)
    explicit_namespace = str(cfg.get("namespace") or "").strip()
    if explicit_namespace:
        return explicit_namespace[:128]
    workspace_root = str(state.get("workspace_root") or "").strip()
    if workspace_root:
        workspace_hash = hashlib.sha256(workspace_root.encode("utf-8")).hexdigest()[:20]
        return f"ws:{workspace_hash}"
    return "default"


def _redis() -> Optional[Any]:  # returns redis.Redis instance or None
    global _redis_client
    if redis is None:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        _redis_client = redis.Redis.from_url(_REDIS_URL, decode_responses=True)
        _redis_client.ping()
    except Exception as exc:  # pragma: no cover
        logger.debug("cross_task_memory: redis unavailable (%s)", exc)
        _redis_client = False
    return _redis_client if _redis_client else None


def _list_key(namespace: str) -> str:
    return f"swarm:xmem:{namespace}"


def _max_items(state: Mapping[str, Any]) -> int:
    cfg = _mem_cfg(state)
    try:
        max_count = int(cfg.get("max_list_items") or _MEM_LIST_MAX)
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
    normalized = {
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
        normalized = {
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
        items = [str(x).strip() for x in list(structured.get(key) or []) if str(x).strip()]
        if not items:
            continue
        title = key.replace("_", " ").title()
        parts.append(f"## {title}")
        for item in items:
            parts.append(f"- {item}")
        parts.append("")
    return "\n".join(parts).strip()


def _build_episode_payload(
    *,
    step_id: str,
    body: str,
    task_id: str,
    artifact: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    structured = normalize_memory_artifact(artifact)
    if not structured:
        structured = _parse_structured_memory_body(body)
    rendered_body = _render_structured_memory(structured)
    if not rendered_body:
        rendered_body = (body or "").strip()
    return {
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
    }


def append_episode(
    state: Mapping[str, Any],
    *,
    step_id: str,
    body: str,
    task_id: str = "",
    artifact: Mapping[str, Any] | None = None,
) -> None:
    if not cross_task_memory_enabled(state):
        return
    text = (body or "").strip()
    if not text and not normalize_memory_artifact(artifact):
        return
    ns = memory_namespace(state)
    max_items = _max_items(state)
    episode = _build_episode_payload(step_id=step_id, body=text, task_id=task_id, artifact=artifact)
    payload = json.dumps(episode, ensure_ascii=False)
    redis_client = _redis()
    if redis_client:
        key = _list_key(ns)
        redis_client.lpush(key, payload)
        redis_client.ltrim(key, 0, max_items - 1)
        return
    bucket = _LOCAL_EPISODES.setdefault(ns, [])
    bucket.insert(0, episode)
    del bucket[max_items:]


def persist_after_pipeline_step(
    step_id: str,
    state: Mapping[str, Any],
    step_delta: Mapping[str, Any],
) -> None:
    if not cross_task_memory_enabled(state):
        return
    cfg = _mem_cfg(state)
    steps = cfg.get("persist_steps")
    if isinstance(steps, list):
        allowed = {str(s).strip() for s in steps if str(s).strip()}
    else:
        allowed = _DEFAULT_PERSIST_STEPS
    if step_id not in allowed:
        return

    from backend.App.orchestration.application.pipeline_graph import ARTIFACT_AGENT_OUTPUT_KEYS

    out_key: Optional[str] = None
    for artifact_step_id, key in ARTIFACT_AGENT_OUTPUT_KEYS:
        if artifact_step_id == step_id:
            out_key = key
            break
    if not out_key and step_id.startswith("crole_"):
        out_key = f"{step_id}_output"
    if not out_key:
        return
    artifact_key = memory_artifact_state_key(step_id)
    artifact = normalize_memory_artifact(step_delta.get(artifact_key))
    if not artifact and artifact_key:
        artifact = normalize_memory_artifact(state.get(artifact_key))
    body = step_delta.get(out_key)
    if body is None:
        body = state.get(out_key) if isinstance(state, Mapping) else None
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


def _normalize_token(s: str) -> list[str]:
    s = s.lower()
    return [t for t in re.split(r"[^\w]+", s) if len(t) >= 3]


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


def _score_episode(query: str, body: str) -> float:
    q_tokens = set(_normalize_token(query))
    if not q_tokens:
        q_tokens = set(_normalize_token(query[:200]))
    tokens = set(_normalize_token(body))
    inter = len(q_tokens & tokens)
    score = float(inter)
    ql = query.lower().strip()
    bl = body.lower()
    if ql and ql in bl:
        score += 4.0
    return score


def search_episodes(
    state: Mapping[str, Any],
    query: str,
    *,
    limit: int = 8,
) -> list[tuple[dict[str, Any], float]]:
    if not cross_task_memory_enabled(state):
        return []
    cfg = _mem_cfg(state)
    ns = memory_namespace(state)
    pool_size = int(cfg.get("retrieve_pool") or 80)
    pool_size = max(20, min(500, pool_size))
    episodes = _load_episodes(ns, limit=pool_size)
    scored: list[tuple[dict[str, Any], float]] = []
    for episode in episodes:
        body = str(episode.get("body") or "")
        relevance_score = _score_episode(query, body)
        if relevance_score > 0:
            scored.append((episode, relevance_score))
    scored.sort(key=lambda item: -item[1])
    lim = max(1, min(30, limit))
    return scored[:lim]


def should_inject_at_step(state: Mapping[str, Any], step_id: str) -> bool:
    if not cross_task_memory_enabled(state):
        return False
    cfg = _mem_cfg(state)
    raw = cfg.get("inject_at_steps")
    if isinstance(raw, list) and raw:
        return step_id in {str(x).strip() for x in raw if str(x).strip()}
    return step_id in _DEFAULT_INJECT_AT_STEPS


def format_cross_task_memory_block(
    state: Mapping[str, Any],
    query: str,
    *,
    current_step: str = "pm",
) -> str:
    if not should_inject_at_step(state, current_step):
        return ""
    cfg = _mem_cfg(state)
    try:
        retrieve_limit = int(cfg.get("retrieve_limit") or 6)
    except (TypeError, ValueError):
        retrieve_limit = 6
    try:
        max_chars = int(cfg.get("max_inject_chars") or 8000)
    except (TypeError, ValueError):
        max_chars = 8000
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

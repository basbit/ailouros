from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_redis_mod: Optional[Any] = None
try:
    import redis as _redis_mod_import
    _redis_mod = _redis_mod_import
except ImportError:
    pass

_REDIS_URL = (
    os.getenv("SWARM_REDIS_URL")
    or os.getenv("REDIS_URL")
    or "redis://localhost:6379/0"
).strip()
_TTL = 3600

_redis_client: Any = None
_redis_unavailable = False


def _redis() -> Any:
    global _redis_client, _redis_unavailable
    if _redis_unavailable or _redis_mod is None:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        _redis_client = _redis_mod.Redis.from_url(_REDIS_URL, decode_responses=True)
        _redis_client.ping()
        return _redis_client
    except Exception as exc:
        logger.warning(
            "approval_store: Redis unavailable at %s — using in-process memory only. "
            "Approvals will not persist across restarts. Error: %s",
            _REDIS_URL, exc,
        )
        _redis_unavailable = True
        return None


_LOCAL_STORE: dict[str, str] = {}


def store_pending(gate_type: str, task_id: str, data: Any) -> None:
    key = f"swarm:approval:{gate_type}:{task_id}"
    payload = json.dumps(data, ensure_ascii=False)
    r = _redis()
    if r is not None:
        try:
            r.setex(key, _TTL, payload)
            return
        except Exception as exc:
            logger.warning("approval_store: Redis set failed: %s", exc)
    _LOCAL_STORE[key] = payload


def load_pending(gate_type: str, task_id: str) -> Optional[Any]:
    key = f"swarm:approval:{gate_type}:{task_id}"
    r = _redis()
    if r is not None:
        try:
            raw = r.get(key)
            if raw is not None:
                return json.loads(raw)
            return None
        except Exception as exc:
            logger.warning("approval_store: Redis get failed: %s", exc)
    raw_local = _LOCAL_STORE.get(key)
    if raw_local is not None:
        return json.loads(raw_local)
    return None


def clear_pending(gate_type: str, task_id: str) -> None:
    key = f"swarm:approval:{gate_type}:{task_id}"
    r = _redis()
    if r is not None:
        try:
            r.delete(key)
        except Exception as exc:
            logger.warning("approval_store: Redis delete failed for %s: %s", key, exc)
    _LOCAL_STORE.pop(key, None)


def store_result(task_id: str, approved: bool, user_input: str = "") -> None:
    key = f"swarm:approval:result:{task_id}"
    payload = json.dumps({"approved": approved, "user_input": user_input}, ensure_ascii=False)
    r = _redis()
    if r is not None:
        try:
            r.setex(key, _TTL, payload)
            return
        except Exception as exc:
            logger.warning("approval_store: Redis setex failed for result %s: %s", key, exc)
    _LOCAL_STORE[key] = payload


def load_result(task_id: str) -> Optional[dict[str, Any]]:
    key = f"swarm:approval:result:{task_id}"
    r = _redis()
    if r is not None:
        try:
            raw = r.get(key)
            if raw is not None:
                return json.loads(raw)
            return None
        except Exception as exc:
            logger.warning("approval_store: Redis get failed for result %s: %s", key, exc)
    raw_local = _LOCAL_STORE.get(key)
    if raw_local is not None:
        return json.loads(raw_local)
    return None


def clear_result(task_id: str) -> None:
    key = f"swarm:approval:result:{task_id}"
    r = _redis()
    if r is not None:
        try:
            r.delete(key)
        except Exception as exc:
            logger.warning("approval_store: Redis delete failed for result %s: %s", key, exc)
    _LOCAL_STORE.pop(key, None)

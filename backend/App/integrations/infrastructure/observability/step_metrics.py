from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Mapping
from typing import Any, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_step_counts: dict[str, int] = defaultdict(int)
_step_duration_ms: dict[str, list[float]] = defaultdict(list)
_max_samples = 200
_task_last: dict[str, str] = {}
_role_model_counts: dict[tuple[str, str], int] = defaultdict(int)
_task_step_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
_task_step_duration_ms: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
_task_step_token_totals: dict[str, dict[str, dict[str, int]]] = defaultdict(
    lambda: defaultdict(
        lambda: {
            "input_tokens": 0, "output_tokens": 0,
            "retrieved_tokens": 0, "tool_calls_count": 0,
            "file_read_cache_hits": 0, "file_read_cache_misses": 0,
        }
    )
)
_task_role_model_counts: dict[str, dict[tuple[str, str], int]] = defaultdict(lambda: defaultdict(int))
_step_token_totals: dict[str, dict[str, int]] = defaultdict(lambda: {
    "input_tokens": 0, "output_tokens": 0,
    "retrieved_tokens": 0, "tool_calls_count": 0,
    "file_read_cache_hits": 0, "file_read_cache_misses": 0,
})

_TOKEN_KEY_INPUT = "_step_input_tokens"
_TOKEN_KEY_OUTPUT = "_step_output_tokens"
_TOKEN_KEY_RETRIEVED = "_step_retrieved_tokens"
_TOKEN_KEY_RETRIEVED_BYTES = "_step_retrieved_bytes"
_TOKEN_KEY_TOOL_CALLS = "_step_tool_calls_count"
_TOKEN_KEY_FILE_READ_CACHE_HITS = "_step_file_read_cache_hits"
_TOKEN_KEY_FILE_READ_CACHE_MISSES = "_step_file_read_cache_misses"


def _guess_model(delta: Mapping[str, Any]) -> str:
    for k, v in delta.items():
        if isinstance(k, str) and k.endswith("_model") and isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _extract_token_metrics(delta: Mapping[str, Any]) -> dict[str, Any]:
    def _int_or_none(key: str) -> Optional[int]:
        v = delta.get(key)
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    return {
        "input_tokens": _int_or_none(_TOKEN_KEY_INPUT),
        "output_tokens": _int_or_none(_TOKEN_KEY_OUTPUT),
        "retrieved_tokens": _int_or_none(_TOKEN_KEY_RETRIEVED),
        "retrieved_bytes": _int_or_none(_TOKEN_KEY_RETRIEVED_BYTES),
        "tool_calls_count": _int_or_none(_TOKEN_KEY_TOOL_CALLS),
        "file_read_cache_hits": _int_or_none(_TOKEN_KEY_FILE_READ_CACHE_HITS),
        "file_read_cache_misses": _int_or_none(_TOKEN_KEY_FILE_READ_CACHE_MISSES),
    }


def record_step(
    step_id: str,
    duration_ms: float,
    *,
    task_id: str = "",
    step_delta: Optional[Mapping[str, Any]] = None,
) -> None:
    delta = step_delta or {}
    tokens = _extract_token_metrics(delta)
    with _lock:
        _step_counts[step_id] += 1
        bucket = _step_duration_ms[step_id]
        bucket.append(duration_ms)
        if len(bucket) > _max_samples:
            del bucket[: len(bucket) - _max_samples]
        if task_id:
            _task_last[step_id] = task_id
            _task_step_counts[task_id][step_id] += 1
            task_bucket = _task_step_duration_ms[task_id][step_id]
            task_bucket.append(duration_ms)
            if len(task_bucket) > _max_samples:
                del task_bucket[: len(task_bucket) - _max_samples]
        model = _guess_model(delta)
        if model:
            _role_model_counts[(step_id, model)] += 1
            if task_id:
                _task_role_model_counts[task_id][(step_id, model)] += 1
        for k in (
            "input_tokens", "output_tokens", "retrieved_tokens", "tool_calls_count",
            "file_read_cache_hits", "file_read_cache_misses",
        ):
            v = tokens.get(k)
            if v:
                _step_token_totals[step_id][k] += v
                if task_id:
                    _task_step_token_totals[task_id][step_id][k] += v

    has_tokens = any(v is not None and v > 0 for v in tokens.values())
    if has_tokens:
        logger.info(
            "step_metrics: step=%s task=%s duration_ms=%.0f "
            "input_tokens=%s output_tokens=%s retrieved_tokens=%s tool_calls=%s",
            step_id, task_id, duration_ms,
            tokens["input_tokens"], tokens["output_tokens"],
            tokens["retrieved_tokens"], tokens["tool_calls_count"],
        )
    else:
        logger.debug(
            "step_metrics: step=%s task=%s duration_ms=%.0f (no token data)",
            step_id, task_id, duration_ms,
        )

    try:
        from backend.App.integrations.infrastructure.observability.prometheus import (
            observe_pipeline_step,
        )

        observe_pipeline_step(step_id, duration_ms, step_delta)
    except Exception as prometheus_exc:
        logger.debug("step_metrics: prometheus observe failed: %s", prometheus_exc)


def snapshot() -> dict[str, Any]:
    with _lock:
        durs = {}
        for sid, samples in _step_duration_ms.items():
            if not samples:
                continue
            s = sorted(samples)
            mid = s[len(s) // 2]
            durs[sid] = {
                "count": _step_counts.get(sid, 0),
                "p50_ms": round(mid, 2),
                "max_ms": round(max(s), 2),
                "tokens": dict(_step_token_totals.get(sid, {})),
            }
        rm = [
            {"step": a, "model": b, "calls": c}
            for (a, b), c in sorted(_role_model_counts.items(), key=lambda x: -x[1])[:80]
        ]
        return {
            "steps": durs,
            "role_model_top": rm,
            "updated_at": time.time(),
        }


def snapshot_for_task(task_id: str) -> dict[str, Any]:
    task_key = str(task_id or "").strip()
    if not task_key:
        return {"steps": {}, "role_model_top": [], "updated_at": time.time()}
    with _lock:
        task_steps = {}
        for sid, samples in _task_step_duration_ms.get(task_key, {}).items():
            if not samples:
                continue
            s = sorted(samples)
            mid = s[len(s) // 2]
            task_steps[sid] = {
                "count": _task_step_counts.get(task_key, {}).get(sid, 0),
                "p50_ms": round(mid, 2),
                "max_ms": round(max(s), 2),
                "tokens": dict(_task_step_token_totals.get(task_key, {}).get(sid, {})),
            }
        rm = [
            {"step": a, "model": b, "calls": c}
            for (a, b), c in sorted(
                _task_role_model_counts.get(task_key, {}).items(),
                key=lambda x: -x[1],
            )[:80]
        ]
        return {
            "steps": task_steps,
            "role_model_top": rm,
            "updated_at": time.time(),
        }


def reset_for_tests() -> None:
    with _lock:
        _step_counts.clear()
        _step_duration_ms.clear()
        _task_last.clear()
        _role_model_counts.clear()
        _step_token_totals.clear()
        _task_step_counts.clear()
        _task_step_duration_ms.clear()
        _task_step_token_totals.clear()
        _task_role_model_counts.clear()

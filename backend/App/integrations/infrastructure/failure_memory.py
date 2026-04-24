from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from backend.App.shared.infrastructure.json_file_io import read_json_file, write_json_file

logger = logging.getLogger(__name__)

__all__ = [
    "failure_memory_enabled",
    "failure_memory_path_for_state",
    "record_failure",
    "get_warnings_for",
    "format_failure_warnings_block",
]

_STORE_LOCK = threading.Lock()


def failure_memory_enabled() -> bool:
    return os.getenv("SWARM_FAILURE_MEMORY", "1").strip() not in ("0", "false", "no", "off")


def _max_entries() -> int:
    raw = os.getenv("SWARM_FAILURE_MEMORY_MAX_ENTRIES", "200").strip()
    try:
        return max(10, int(raw))
    except (ValueError, TypeError):
        return 200


def _warn_limit() -> int:
    raw = os.getenv("SWARM_FAILURE_MEMORY_WARN_LIMIT", "3").strip()
    try:
        return max(1, int(raw))
    except (ValueError, TypeError):
        return 3


def _min_score() -> float:
    raw = os.getenv("SWARM_FAILURE_MEMORY_MIN_SCORE", "2.0").strip()
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 2.0


def failure_memory_path_for_state(state: Mapping[str, Any]) -> Path:
    path_str = ""
    agent_config = state.get("agent_config")
    if isinstance(agent_config, dict):
        swarm_cfg = agent_config.get("swarm")
        if isinstance(swarm_cfg, dict):
            path_str = str(swarm_cfg.get("failure_memory_path") or "").strip()
    if not path_str:
        path_str = os.getenv("SWARM_FAILURE_MEMORY_PATH", "").strip()
    if path_str:
        p = Path(path_str).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        return p.resolve()
    return (Path.cwd() / ".swarm" / "failure_memory.json").resolve()


def _tokenize(text: str) -> set[str]:
    text = text.lower()
    return {t for t in re.split(r"[^\w]+", text) if len(t) >= 3}


def _token_score(query: str, candidate: str) -> float:
    q_toks = _tokenize(query[:2000])
    c_toks = _tokenize(candidate)
    if not q_toks or not c_toks:
        return 0.0
    overlap = len(q_toks & c_toks)
    bonus = 2.0 if query[:200].lower() in candidate.lower() else 0.0
    return float(overlap) + bonus


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "failures": []}
    try:
        data = read_json_file(path)
        if not isinstance(data, dict) or not isinstance(data.get("failures"), list):
            return {"version": 1, "failures": []}
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("failure_memory: load failed (%s): %s", path, exc)
        return {"version": 1, "failures": []}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_file(path, data)


def _fingerprint(step: str, summary: str) -> str:
    raw = f"{step}\x00{summary[:300]}".strip()
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def record_failure(
    state: Mapping[str, Any],
    step: str,
    summary: str,
    context: str = "",
) -> None:
    if not failure_memory_enabled():
        return
    fp = _fingerprint(step, summary)
    path = failure_memory_path_for_state(state)
    with _STORE_LOCK:
        data = _load(path)
        failures: list[dict[str, Any]] = data["failures"]
        existing = next((f for f in failures if f.get("fingerprint") == fp), None)
        if existing is not None:
            existing["count"] = int(existing.get("count") or 1) + 1
            existing["last_seen"] = time.time()
            if context.strip():
                existing["context"] = context[:500]
        else:
            entry: dict[str, Any] = {
                "fingerprint": fp,
                "step": step,
                "summary": summary[:200],
                "context": context[:500],
                "count": 1,
                "last_seen": time.time(),
            }
            failures.append(entry)
        max_n = _max_entries()
        if len(failures) > max_n:
            failures.sort(key=lambda f: f.get("last_seen", 0.0))
            data["failures"] = failures[-max_n:]
        _save(path, data)
    logger.debug(
        "failure_memory: recorded failure step=%r summary=%r fp=%s",
        step, summary[:60], fp,
    )


def get_warnings_for(
    state: Mapping[str, Any],
    prompt_text: str,
    *,
    step: Optional[str] = None,
    limit: Optional[int] = None,
    min_score: Optional[float] = None,
) -> list[dict[str, Any]]:
    if not failure_memory_enabled():
        return []
    k = limit if limit is not None else _warn_limit()
    threshold = min_score if min_score is not None else _min_score()
    path = failure_memory_path_for_state(state)
    with _STORE_LOCK:
        data = _load(path)
    failures = data.get("failures") or []
    scored: list[tuple[dict[str, Any], float]] = []
    for entry in failures:
        if step and entry.get("step") != step:
            continue
        candidate_text = (
            str(entry.get("summary") or "") + " " + str(entry.get("context") or "")
        )
        sc = _token_score(prompt_text, candidate_text)
        if sc >= threshold:
            scored.append(({**entry, "score": sc}, sc))
    scored.sort(key=lambda t: -t[1])
    return [item for item, _ in scored[:k]]


def format_failure_warnings_block(
    state: Mapping[str, Any],
    prompt_text: str,
    *,
    step: Optional[str] = None,
    limit: Optional[int] = None,
    min_score: Optional[float] = None,
) -> str:
    warnings = get_warnings_for(
        state, prompt_text, step=step, limit=limit, min_score=min_score
    )
    if not warnings:
        return ""
    lines = ["[Past failure warnings — address these before responding]\n"]
    for w in warnings:
        s = str(w.get("step") or "?")
        summary = str(w.get("summary") or "")
        count = int(w.get("count") or 1)
        count_note = f" (seen {count}×)" if count > 1 else ""
        lines.append(f"WARNING [{s}]{count_note}: {summary}\n")
    return "".join(lines) + "\n"

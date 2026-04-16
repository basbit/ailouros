"""H-7 — FailureMemory: persistent store of past pipeline failures.

Records step-level failures (NEEDS_WORK exhaustion, runtime exceptions,
validation errors) keyed by a content-addressed fingerprint of the failure
context.  Before starting any step, callers query for similar past failures
and inject a concise warning block into the prompt so the model avoids
repeating the same mistake.

Storage
-------
File-backed JSON at ``.swarm/failure_memory.json`` (same directory as other
swarm files).  Override via ``SWARM_FAILURE_MEMORY_PATH`` or
``agent_config.swarm.failure_memory_path``.

Schema::

    {
      "version": 1,
      "failures": [
        {
          "fingerprint": "<sha256[:16]>",
          "step":        "dev_lead",
          "summary":     "missing deliverables.must_exist_files",
          "context":     "<first 500 chars of the triggering prompt>",
          "count":       3,
          "last_seen":   1713123456.0
        },
        …
      ]
    }

Search
------
Hybrid token-overlap scoring (same approach as PatternMemory).  Embeddings
are NOT used here: failure records are short and search is against the
incoming prompt text, which is already at hand — token overlap is fast and
good enough.

Env vars
--------
SWARM_FAILURE_MEMORY=1            (default ON) — master toggle.
SWARM_FAILURE_MEMORY_PATH         — override storage path.
SWARM_FAILURE_MEMORY_MAX_ENTRIES  — cap on stored records (default 200).
SWARM_FAILURE_MEMORY_WARN_LIMIT   — max warnings injected per call (default 3).
SWARM_FAILURE_MEMORY_MIN_SCORE    — minimum score to include a warning (default 2.0).
"""
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

logger = logging.getLogger(__name__)

__all__ = [
    "failure_memory_enabled",
    "failure_memory_path_for_state",
    "record_failure",
    "get_warnings_for",
    "format_failure_warnings_block",
]

_STORE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def failure_memory_enabled() -> bool:
    """Return True when SWARM_FAILURE_MEMORY is not explicitly disabled."""
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
    """Resolve the failure_memory.json path from state or env."""
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


# ---------------------------------------------------------------------------
# Token scoring (same lightweight approach as PatternMemory)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    text = text.lower()
    return {t for t in re.split(r"[^\w]+", text) if len(t) >= 3}


def _token_score(query: str, candidate: str) -> float:
    q_toks = _tokenize(query[:2000])
    c_toks = _tokenize(candidate)
    if not q_toks or not c_toks:
        return 0.0
    overlap = len(q_toks & c_toks)
    # Substring bonus — exact multi-word phrase match is very strong signal
    bonus = 2.0 if query[:200].lower() in candidate.lower() else 0.0
    return float(overlap) + bonus


# ---------------------------------------------------------------------------
# Storage IO — small JSON, O(n) is fine up to _max_entries() = 200
# ---------------------------------------------------------------------------


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "failures": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("failures"), list):
            return {"version": 1, "failures": []}
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("failure_memory: load failed (%s): %s", path, exc)
        return {"version": 1, "failures": []}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fingerprint(step: str, summary: str) -> str:
    """16-char content-addressed key for deduplication."""
    raw = f"{step}\x00{summary[:300]}".strip()
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Public write API
# ---------------------------------------------------------------------------


def record_failure(
    state: Mapping[str, Any],
    step: str,
    summary: str,
    context: str = "",
) -> None:
    """Persist a pipeline failure for future warning injection.

    Idempotent on (step, summary) — repeated identical failures increment
    ``count`` and update ``last_seen`` rather than adding a new entry.

    Args:
        state:   Pipeline state (used to resolve storage path).
        step:    Pipeline step name (e.g. "dev_lead", "dev", "qa").
        summary: Short description of what went wrong (≤200 chars).
        context: The prompt or task text that triggered the failure (first
                 500 chars stored for scoring; helps with retrieval).
    """
    if not failure_memory_enabled():
        return
    fp = _fingerprint(step, summary)
    path = failure_memory_path_for_state(state)
    with _STORE_LOCK:
        data = _load(path)
        failures: list[dict[str, Any]] = data["failures"]
        # Find existing entry by fingerprint
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
        # Trim to max_entries — drop oldest entries first
        max_n = _max_entries()
        if len(failures) > max_n:
            failures.sort(key=lambda f: f.get("last_seen", 0.0))
            data["failures"] = failures[-max_n:]
        _save(path, data)
    logger.debug(
        "failure_memory: recorded failure step=%r summary=%r fp=%s",
        step, summary[:60], fp,
    )


# ---------------------------------------------------------------------------
# Public read API
# ---------------------------------------------------------------------------


def get_warnings_for(
    state: Mapping[str, Any],
    prompt_text: str,
    *,
    step: Optional[str] = None,
    limit: Optional[int] = None,
    min_score: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Return past failure entries relevant to *prompt_text*.

    Scores each stored failure against *prompt_text* using token overlap
    over the failure summary and context.  Returns up to *limit* entries
    with score >= *min_score*, sorted by descending score.

    Args:
        state:       Pipeline state.
        prompt_text: The prompt being built (used as search query).
        step:        When provided, only failures for this step are considered.
        limit:       Max warnings to return (defaults to SWARM_FAILURE_MEMORY_WARN_LIMIT).
        min_score:   Score threshold (defaults to SWARM_FAILURE_MEMORY_MIN_SCORE).

    Returns:
        List of failure dicts with at minimum ``step``, ``summary``,
        ``count``, and ``score`` keys.  Empty list when nothing matches.
    """
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
    """Return a prompt-ready warning block derived from past failures.

    Returns empty string when failure memory is disabled, no relevant
    warnings exist, or the block would be empty.

    Args:
        state:       Pipeline state.
        prompt_text: Current prompt text used as search query.
        step:        Filter to failures from this step only.
        limit:       Max warnings to embed.
        min_score:   Minimum relevance score.
    """
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

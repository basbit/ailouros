"""Persistent JSONL session transcript writer.

After every pipeline step completes, appends one JSON line to:
    var/artifacts/{task_id}/session_transcript.jsonl

Each line is a self-contained JSON object — newline-delimited JSON (NDJSON)
so the file can be tailed, streamed, or imported into any log tool.

Environment:
    SWARM_ARTIFACTS_DIR — root for artifact dirs (default: ``var/artifacts``)
    SWARM_TRANSCRIPT_OUTPUT_PREVIEW_CHARS — max chars for *_output preview fields
        (default: 500; set 0 to disable previews)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Keys whose values (if str) get a truncated preview written to the transcript.
_OUTPUT_KEY_SUFFIX = "_output"
_DEFAULT_PREVIEW_CHARS = int(os.getenv("SWARM_TRANSCRIPT_OUTPUT_PREVIEW_CHARS", "500"))

# Model/provider keys to capture verbatim (no truncation needed).
_PASSTHROUGH_KEYS = frozenset({
    "dev_model", "dev_provider",
    "ba_model", "ba_provider",
    "arch_model", "arch_provider",
    "pm_model", "pm_provider",
    "qa_model", "qa_provider",
    "devops_model", "devops_provider",
    "dev_lead_model", "dev_lead_provider",
})


def _artifacts_root() -> Path:
    """Resolve artifact root at call time so env overrides are picked up.

    Anchored to the app root (see ``backend/App/paths.py``) so CWD changes
    across server restarts do not point at a different directory.
    """
    from backend.App.paths import artifacts_root as _anchored
    return _anchored()


def append_transcript_entry(
    step_id: str,
    state: Any,
    step_delta: dict[str, Any],
    *,
    elapsed_ms: float | None = None,
) -> None:
    """Append one JSONL line for *step_id* to ``session_transcript.jsonl``.

    Silently skips when ``state`` has no ``task_id`` or when the filesystem
    write fails (transcript is non-critical — pipeline must not break).

    Args:
        step_id:    Pipeline step identifier (e.g. "pm", "ba", "dev").
        state:      Full pipeline state after the step completed.
        step_delta: Dict returned by the step function (partial deltas only).
        elapsed_ms: Elapsed wall-clock time for the step in milliseconds.
    """
    task_id = (state.get("task_id") or "").strip()
    if not task_id:
        return

    # Read at call time so patch.dict(os.environ, ...) in tests takes effect.
    preview_chars = int(os.getenv("SWARM_TRANSCRIPT_OUTPUT_PREVIEW_CHARS", str(_DEFAULT_PREVIEW_CHARS)))

    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "step": step_id,
        "elapsed_ms": round(elapsed_ms, 1) if elapsed_ms is not None else None,
        "output_keys": sorted(step_delta.keys()) if step_delta else [],
    }

    # Output previews — truncated text snapshots of *_output fields
    if preview_chars > 0:
        for key, val in (step_delta or {}).items():
            if key.endswith(_OUTPUT_KEY_SUFFIX) and isinstance(val, str):
                text = val.strip()
                if text:
                    entry[key + "_preview"] = text[:preview_chars]

    # Model / provider passthrough (short strings — no truncation)
    for key in _PASSTHROUGH_KEYS:
        if key in step_delta:
            val = step_delta[key]
            if val:
                entry[key] = str(val)

    # Token usage (injected by step_decorator after step returns)
    for token_key in ("_step_input_tokens", "_step_output_tokens"):
        if token_key in step_delta:
            entry[token_key] = step_delta[token_key]

    transcript_path = _artifacts_root() / task_id / "session_transcript.jsonl"
    try:
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        with transcript_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.debug(
            "session_transcript: appended step=%s task_id=%s path=%s",
            step_id, task_id, transcript_path,
        )
    except OSError as exc:
        logger.warning(
            "session_transcript: failed to write step=%s task_id=%s: %s",
            step_id, task_id, exc,
        )

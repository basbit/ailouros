from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from backend.App.shared.application.datetime_utils import utc_now_iso

logger = logging.getLogger(__name__)

_OUTPUT_KEY_SUFFIX = "_output"
_DEFAULT_PREVIEW_CHARS = int(os.getenv("SWARM_TRANSCRIPT_OUTPUT_PREVIEW_CHARS", "500"))

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
    from backend.App.paths import artifacts_root as _anchored
    return _anchored()


def append_transcript_entry(
    step_id: str,
    state: Any,
    step_delta: dict[str, Any],
    *,
    elapsed_ms: float | None = None,
) -> None:
    task_id = (state.get("task_id") or "").strip()
    if not task_id:
        return

    preview_chars = int(os.getenv("SWARM_TRANSCRIPT_OUTPUT_PREVIEW_CHARS", str(_DEFAULT_PREVIEW_CHARS)))

    entry: dict[str, Any] = {
        "ts": utc_now_iso(),
        "task_id": task_id,
        "step": step_id,
        "elapsed_ms": round(elapsed_ms, 1) if elapsed_ms is not None else None,
        "output_keys": sorted(step_delta.keys()) if step_delta else [],
    }

    if preview_chars > 0:
        for key, val in (step_delta or {}).items():
            if key.endswith(_OUTPUT_KEY_SUFFIX) and isinstance(val, str):
                text = val.strip()
                if text:
                    entry[key + "_preview"] = text[:preview_chars]

    for key in _PASSTHROUGH_KEYS:
        if key in step_delta:
            val = step_delta[key]
            if val:
                entry[key] = str(val)

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

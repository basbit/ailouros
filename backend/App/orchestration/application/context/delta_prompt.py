from __future__ import annotations

import hashlib
import os
import threading
from typing import Optional

__all__ = [
    "delta_prompting_enabled",
    "store_artifact",
    "resolve_artifact",
    "artifact_header",
    "build_dialogue_agent_delta_input",
    "build_reviewer_history_compact",
    "build_dev_lead_delta_retry_prompt",
]

_STORE: dict[str, str] = {}
_STORE_LOCK = threading.Lock()


def delta_prompting_enabled() -> bool:
    return os.getenv("SWARM_DELTA_PROMPTING", "1").strip() not in ("0", "false", "no", "off")


def store_artifact(text: str) -> str:
    sha = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    with _STORE_LOCK:
        _STORE.setdefault(sha, text)
    return f"artifact:sha256:{sha}"


def resolve_artifact(ref: str) -> Optional[str]:
    if not ref.startswith("artifact:sha256:"):
        return None
    sha = ref[len("artifact:sha256:"):]
    with _STORE_LOCK:
        return _STORE.get(sha)


def artifact_header(text: str, max_preview: int = 300) -> str:
    ref = store_artifact(text)
    sha_short = ref[-12:]
    preview = text[:max_preview].replace("\n", " ").strip()
    suffix = "…" if len(text) > max_preview else ""
    return f"[{preview}{suffix}] [ref:{sha_short}] [{len(text)} chars]"


def build_dialogue_agent_delta_input(
    initial_input: str,
    reviewer_feedback: str,
    prev_output: str,
    round_n: int,
    *,
    max_preview: int = 300,
) -> str:
    task_header = artifact_header(initial_input, max_preview=max_preview)
    prev_ref = store_artifact(prev_output)
    prev_short = prev_ref[-12:]
    prev_round = round_n - 1

    return (
        f"## Task (compact reference — full spec was sent in round 1)\n"
        f"{task_header}\n\n"
        f"## Reviewer feedback (round {prev_round}) — address ALL issues below\n"
        f"{reviewer_feedback}\n\n"
        f"## Your round {prev_round} output [{len(prev_output)} chars, ref:{prev_short}] was rejected\n"
        f"Produce your corrected version now. Do NOT re-state the task spec — "
        f"use the compact reference above and fix every issue the reviewer raised."
    )


def build_reviewer_history_compact(history: list[dict]) -> str:
    if not history:
        return ""
    lines = ["## Prior round summaries (compact — full content available via artifact refs)"]
    for item in history:
        rn = item.get("round", "?")
        verdict = item.get("verdict", "?")
        out = str(item.get("output") or "")
        review = str(item.get("review") or "")
        out_short = store_artifact(out)[-12:]
        review_short = store_artifact(review)[-12:]
        out_preview = out[:100].replace("\n", " ")
        review_preview = review[:100].replace("\n", " ")
        lines.append(
            f"  Round {rn}: verdict={verdict} | "
            f"output[{len(out)}c,ref:{out_short}]: {out_preview}… | "
            f"review[{len(review)}c,ref:{review_short}]: {review_preview}…"
        )
    return "\n".join(lines)


def build_dev_lead_delta_retry_prompt(
    prev_output: str,
    missing_sections: list[str],
    user_task: str,
    *,
    max_prev_chars: int = 8000,
) -> str:
    store_artifact(prev_output)  # persist for reference
    prev_preview = prev_output[:max_prev_chars]
    if len(prev_output) > max_prev_chars:
        prev_preview += f"\n…[previous output truncated to {max_prev_chars} chars]"

    missing_str = ", ".join(str(s) for s in missing_sections)
    return (
        f"## RETRY: Dev Lead output is missing required deliverable sections\n"
        f"Missing: {missing_str}\n\n"
        f"## Task (brief)\n{user_task[:500]}\n\n"
        f"## Your previous output — reproduce it and add the missing sections\n"
        f"{prev_preview}\n\n"
        f"## Required action\n"
        f"Add the missing sections ({missing_str}) to the `deliverables` object above. "
        f"Keep ALL tasks unchanged. "
        f"Respond with ONLY a ```json ... ``` block, no text outside."
    )

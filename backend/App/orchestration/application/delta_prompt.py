"""H-2 — Delta prompting: in-memory artifact store + compact re-prompt builders.

On NEEDS_WORK cycles the dialogue_loop and dev_lead re-prompts stop repeating
the full initial_input every round.  Instead:
  - initial_input and previous outputs are stored as sha256-keyed artifacts
  - round 2+ sends: compact artifact reference + reviewer feedback + diff hint
  - controlled by SWARM_DELTA_PROMPTING=1 (default ON)

This module is a pure utility layer — pipeline semantics are unchanged.
Token savings per NEEDS_WORK round: replaces ~10-20 KB of repeated context
with ~300-500 chars of compact artifact references.
"""
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

# Thread-safe in-memory artifact store: sha256_hex → content
_STORE: dict[str, str] = {}
_STORE_LOCK = threading.Lock()


def delta_prompting_enabled() -> bool:
    """Return True when SWARM_DELTA_PROMPTING is not explicitly disabled.

    Default: ON (SWARM_DELTA_PROMPTING=1).
    Disable with SWARM_DELTA_PROMPTING=0 to restore pre-H-2 behaviour.
    """
    return os.getenv("SWARM_DELTA_PROMPTING", "1").strip() not in ("0", "false", "no", "off")


def store_artifact(text: str) -> str:
    """Store *text* and return a stable content-addressed reference.

    Format: ``artifact:sha256:<64-char-hex>``
    Idempotent: same content always produces the same ref.
    Thread-safe.
    """
    sha = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    with _STORE_LOCK:
        _STORE.setdefault(sha, text)
    return f"artifact:sha256:{sha}"


def resolve_artifact(ref: str) -> Optional[str]:
    """Resolve an artifact reference back to its content.

    Returns ``None`` when the ref is unknown or malformed.
    Does NOT raise — callers must handle the None case explicitly.
    """
    if not ref.startswith("artifact:sha256:"):
        return None
    sha = ref[len("artifact:sha256:"):]
    with _STORE_LOCK:
        return _STORE.get(sha)


def artifact_header(text: str, max_preview: int = 300) -> str:
    """Return a compact one-line artifact reference with a brief preview.

    Format: ``[<preview>…] [ref:<sha_short>] [<char_count> chars]``
    Suitable for embedding in prompts where the full text is not needed.
    The full text is stored in the artifact store for potential resolution.
    """
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
    """Build compact input for agent_a on round 2+.

    Replaces the full ``initial_input + prev_output`` re-send (which repeats
    the same 10-20 KB every round) with:
    - compact artifact reference for initial_input (full text stored on round 1)
    - complete reviewer feedback (new information the agent must address)
    - compact reference for the previous output

    Args:
        initial_input: The original task specification (sent in full on round 1).
        reviewer_feedback: Reviewer output from the preceding round.
        prev_output: Agent_a output from the preceding round.
        round_n: Current round number (2+).
        max_preview: Max characters to show in the task preview.

    Returns:
        Compact delta prompt string ready for agent_a.run().
    """
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
    """Build compact history block for reviewer_input (round 2+).

    Instead of embedding the full output + review text for each prior round
    (which can reach 100+ KB), stores each as an artifact and includes only
    a one-line summary with ref.  The reviewer typically only needs the
    latest round's output plus the verdict trend.

    Args:
        history: list of {"round": int, "output": str, "review": str, "verdict": str}

    Returns:
        Compact history section string, or empty string if history is empty.
    """
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
    """Build compact delta re-prompt for a dev_lead retry.

    When the dev_lead output is missing required deliverable sections
    (must_exist_files, spec_symbols, verification_commands, etc.), the
    orchestrator retries.  Instead of rebuilding the full 15-20 KB prompt
    (spec + code_analysis + planning_reviews + workspace_brief), this
    sends only the actionable delta: the previous output + instruction to
    add the missing sections.

    Args:
        prev_output: The rejected dev_lead output (contains the task plan).
        missing_sections: List of required section names that were absent.
        user_task: Brief user task description (for model orientation).
        max_prev_chars: Max characters of prev_output to include inline.

    Returns:
        Compact retry prompt (~500-9000 chars vs 15-20 KB for full rebuild).
    """
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

"""Single entry point for orchestration retry / reprompt prompt builders.

Before this module existed, per-role retry prompts were scattered across:

  * ``context/delta_prompt.py`` — dialogue / dev_lead delta retries
  * ``context/repo_evidence.py`` — repo-evidence failure retries
  * ``nodes/dev_runner.py`` — truncation / refusal retries (inline)
  * ``nodes/dev_lead.py`` — boundary / path-contract retries (inline)
  * ``nodes/qa.py`` — QA retry inline
  * ``nodes/pm.py`` / ``nodes/pm_clarify.py`` — planning retry feedback inline

This module re-exports the existing specialised builders so every call site
can import from one place, and adds a small generic builder
:func:`build_standard_retry_prompt` for the common "wrap feedback + previous
output into a corrective prompt" case. Call sites with highly role-specific
messaging (dev_runner truncation recovery, QA retry with verification
context, …) continue to craft their own prompts — unifying those into a
single function would lose per-role nuance.
"""

from __future__ import annotations

from typing import Optional

# Re-export existing specialised builders so call sites have one import.
from backend.App.orchestration.application.context.delta_prompt import (
    build_dev_lead_delta_retry_prompt,
    build_dialogue_agent_delta_input,
    build_reviewer_history_compact,
)
from backend.App.orchestration.application.context.repo_evidence import (
    _artifact_only_retry_prompt_for_repo_evidence_failure as build_artifact_only_repo_evidence_retry,
    _retry_prompt_for_repo_evidence_failure as build_repo_evidence_retry,
)

__all__ = [
    "build_artifact_only_repo_evidence_retry",
    "build_dev_lead_delta_retry_prompt",
    "build_dialogue_agent_delta_input",
    "build_repo_evidence_retry",
    "build_reviewer_history_compact",
    "build_standard_retry_prompt",
]


def build_standard_retry_prompt(
    feedback: str,
    *,
    role: Optional[str] = None,
    previous_output: Optional[str] = None,
    max_previous_chars: int = 4000,
    instruction_suffix: Optional[str] = None,
) -> str:
    """Assemble a generic "[feedback] + optional previous output + action" retry prompt.

    Use this when the retry reasoning is **feedback-only** — i.e. the caller
    has a human- or reviewer-supplied correction message and wants the
    agent to re-emit output addressing it. For role-specific retries (QA
    verification, dev truncation recovery, etc.) craft the prompt locally
    — this helper is deliberately narrow.

    Args:
        feedback: The correction / critique text to include.
        role: Optional role name used in the header (e.g. ``"pm"``).
        previous_output: If supplied, included (truncated) under a ``"Your
            previous output"`` header so the agent can compare.
        max_previous_chars: Truncate ``previous_output`` at this length.
        instruction_suffix: Optional trailing instruction (e.g.
            "Respond with JSON only.").
    """
    header = f"[{role} retry feedback]" if role else "[retry feedback]"
    parts: list[str] = [f"{header}\n{feedback.strip()}"]

    if previous_output is not None:
        prev = str(previous_output)
        if len(prev) > max_previous_chars:
            prev = (
                prev[:max_previous_chars]
                + f"\n…[previous output truncated to {max_previous_chars} chars]"
            )
        parts.append(f"\n## Your previous output\n{prev}")

    action = instruction_suffix or (
        "Apply corrections and re-run. Do not repeat unchanged material."
    )
    parts.append(f"\n## Required action\n{action}")

    return "\n".join(parts)

"""Self-verification loop for pipeline step outputs (K-1).

Rules (INV-1): both original and re-run attempts are logged explicitly.
Disabled when SWARM_SELF_VERIFY=0 (default).
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Module-level sentinel: False by default. _initial_pipeline_state sets the env var
# SWARM_SELF_VERIFY, and _verify_enabled() reads both the env var AND this module-level
# flag so that unit tests can still monkeypatch the module attribute.
_VERIFY_ENABLED: bool = False
_VERIFY_MODEL: str = ""  # empty = use env SWARM_SELF_VERIFY_MODEL
_VERIFY_MODEL_DEFAULT = os.getenv("SWARM_SELF_VERIFY_MODEL", "claude-haiku-4-5")


def _verify_enabled() -> bool:
    """Return True if self-verify is active.

    Reads env at call time (so that env vars set by _initial_pipeline_state after
    import are honoured) and also checks the module-level _VERIFY_ENABLED flag so
    that unit-test monkeypatching of the module attribute continues to work.
    """
    import sys
    mod = sys.modules[__name__]
    if getattr(mod, "_VERIFY_ENABLED", False):
        return True
    return os.getenv("SWARM_SELF_VERIFY", "0") == "1"


def _verify_model() -> str:
    """Read at call time so that env set by _initial_pipeline_state is honoured."""
    import sys
    mod = sys.modules[__name__]
    mod_val = getattr(mod, "_VERIFY_MODEL", "")
    if mod_val:
        # Test or runtime override via module attribute
        return mod_val
    return os.getenv("SWARM_SELF_VERIFY_MODEL", _VERIFY_MODEL_DEFAULT)


_VERIFY_PROMPT_TMPL = (
    "You are a strict output verifier. Given the task specification and the agent output, "
    "list any issues: missing requirements, contradictions, or logical errors. "
    "If the output satisfies the specification, respond with an empty JSON list: [].\n\n"
    "Task specification:\n{task_spec}\n\nAgent output:\n{output}\n\n"
    "Respond ONLY with a JSON array of issue strings."
)


@dataclass
class VerifyResult:
    passed: bool
    issues: list[str] = field(default_factory=list)


class SelfVerifier:
    """Invokes a lightweight model to verify agent step output.

    Usage:
        verifier = SelfVerifier()
        result = verifier.verify(task_spec="...", output="...")
        if not result.passed:
            # retry with result.issues appended
    """

    def verify(self, task_spec: str, output: str) -> VerifyResult:
        if not _verify_enabled():
            return VerifyResult(passed=True)
        if not task_spec or not output:
            return VerifyResult(passed=True)
        try:
            return self._call_verifier(task_spec, output)
        except Exception as exc:
            logger.error(
                "SelfVerifier: verification call failed — treating as NOT passed "
                "(set SWARM_SELF_VERIFY=0 to disable). Error: %s", exc, exc_info=True,
            )
            return VerifyResult(passed=False, issues=[f"Verification call failed: {exc}"])

    def _call_verifier(self, task_spec: str, output: str) -> VerifyResult:
        from backend.App.integrations.infrastructure.llm.client import chat_completion_text
        import json

        prompt = _VERIFY_PROMPT_TMPL.format(task_spec=task_spec[:4000], output=output[:8000])
        raw = chat_completion_text(
            model=_verify_model(),
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            issues = json.loads(raw.strip())
            if not isinstance(issues, list):
                issues = []
        except (json.JSONDecodeError, ValueError):
            issues = []

        passed = len(issues) == 0
        if not passed:
            logger.info("SelfVerifier: issues found: %s", issues)  # INV-1
        return VerifyResult(passed=passed, issues=[str(i) for i in issues])


def run_with_self_verify(
    agent_fn: Callable[..., str],
    task_spec: str,
    *args: Any,
    **kwargs: Any,
) -> str:
    """Run agent_fn, verify output, retry once if issues found (INV-1).

    Args:
        agent_fn: callable that returns a string output
        task_spec: task specification text used for verification prompt
        *args, **kwargs: forwarded to agent_fn

    Returns:
        Final agent output string (re-run or original).
    """
    output = agent_fn(*args, **kwargs)
    logger.debug("SelfVerifier: first attempt complete, length=%d", len(output))

    if not _verify_enabled():
        return output

    verifier = SelfVerifier()
    result = verifier.verify(task_spec=task_spec, output=output)
    if result.passed:
        return output

    # Re-run once with issues appended (INV-1: both attempts logged)
    issues_text = "\n".join(f"- {i}" for i in result.issues)
    logger.info("SelfVerifier: re-running agent due to issues:\n%s", issues_text)  # INV-1

    # Inject issues into the first string argument (user_input), not as a kwarg
    augmented_args = list(args)
    if augmented_args and isinstance(augmented_args[0], str):
        augmented_args[0] = augmented_args[0] + f"\n\nPrevious attempt issues:\n{issues_text}"
        rerun_output = agent_fn(*augmented_args, **kwargs)
    else:
        # Cannot inject issues safely — log and return original output
        logger.warning(
            "SelfVerifier: cannot inject issues (first arg is not str), returning original output"
        )  # INV-1
        return output

    logger.info("SelfVerifier: re-run complete, length=%d", len(rerun_output))  # INV-1
    return rerun_output

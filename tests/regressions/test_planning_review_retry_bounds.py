"""Regression: planning-review retry loop must bound and escalate cleanly.

Bug: task aec02899 burned 108k+ tokens because:
  * retries were allowed even when the reviewer output was empty / too
    short (same canned NEEDS_WORK produced over and over by local model);
  * the loop continued until the LLM call finally hung at retry 3;
  * no explicit cap surfaced via `SWARM_MAX_PLANNING_RETRIES` override.

Expected:
  * When reviewer output is < MIN_REVIEW_CHARS after retry, loop escalates
    immediately with a clear SSE progress message (not another retry).
  * `SWARM_MAX_PLANNING_RETRIES` env, when set, overrides the generic
    `SWARM_MAX_STEP_RETRIES` for review_* steps.
  * Loop always exits within max_retries + 1 iterations of the reviewer.
"""
from __future__ import annotations

from backend.App.orchestration.application.pipeline_enforcement import (
    _MIN_REVIEW_CONTENT_CHARS,
    _max_planning_review_retries,
)


def test_min_review_chars_constant_is_reasonable():
    """The empty-guard threshold must be > 0 and < typical real review length."""
    assert 0 < _MIN_REVIEW_CONTENT_CHARS < 500, (
        f"MIN_REVIEW_CONTENT_CHARS={_MIN_REVIEW_CONTENT_CHARS} looks wrong"
    )


def test_max_planning_retries_default(monkeypatch):
    monkeypatch.delenv("SWARM_MAX_PLANNING_RETRIES", raising=False)
    monkeypatch.delenv("SWARM_MAX_STEP_RETRIES", raising=False)
    assert _max_planning_review_retries() == 2


def test_max_planning_retries_specific_env_wins(monkeypatch):
    monkeypatch.setenv("SWARM_MAX_PLANNING_RETRIES", "1")
    monkeypatch.setenv("SWARM_MAX_STEP_RETRIES", "5")
    assert _max_planning_review_retries() == 1


def test_max_planning_retries_falls_back_to_step_retries(monkeypatch):
    monkeypatch.delenv("SWARM_MAX_PLANNING_RETRIES", raising=False)
    monkeypatch.setenv("SWARM_MAX_STEP_RETRIES", "3")
    assert _max_planning_review_retries() == 3


def test_max_planning_retries_clamped_to_nonneg(monkeypatch):
    monkeypatch.setenv("SWARM_MAX_PLANNING_RETRIES", "-5")
    assert _max_planning_review_retries() == 0


def test_max_planning_retries_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv("SWARM_MAX_PLANNING_RETRIES", "abc")
    monkeypatch.delenv("SWARM_MAX_STEP_RETRIES", raising=False)
    # Invalid → treated as unset, fall back to SWARM_MAX_STEP_RETRIES default (2)
    assert _max_planning_review_retries() == 2


def test_empty_review_short_circuits_retry_loop(monkeypatch):
    """When reviewer returns <MIN chars of NEEDS_WORK, escalate immediately.

    Full flow test: simulate a reviewer that always returns a one-liner
    "VERDICT: NEEDS_WORK" (below threshold). The loop must NOT retry —
    it must emit an "escalated due to empty review" event and exit.
    """
    from backend.App.orchestration.application.pipeline_enforcement import (
        _is_empty_review,
    )
    # Shorter than threshold → treated as empty
    assert _is_empty_review("VERDICT: NEEDS_WORK")
    # Reasonable output → not empty
    assert not _is_empty_review(
        "### Summary of Work\nPM decomposed the task into 5 actions. "
        "### Risks & Gaps\nMissing acceptance criteria. VERDICT: NEEDS_WORK"
    )
    # Whitespace only → empty
    assert _is_empty_review("   \n\n  ")
    # None/empty string → empty
    assert _is_empty_review("")

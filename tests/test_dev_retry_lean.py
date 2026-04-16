"""Regression: ``is_dev_retry_lean`` — §23.3 second-pass Dev prompt leanness.

Policy locked in tests (so future prompt-audit changes don't silently alter
the decision):

* First Dev run (no prior review, no swarm_file re-prompt) → **False**.
  The full ~20 KB prompt with pattern/knowledge/sibling blocks is needed
  because the LM Studio slot cache is cold and the model has no prior
  context to lean on.
* Retry after ``review_dev`` returned NEEDS_WORK → **True**. The reviewer
  already saw the same context; pattern/knowledge/sibling blocks are in
  the slot cache — drop them.
* Re-run triggered by ``_swarm_file_reprompt`` (format enforcement) →
  **True**.
* ``SWARM_DEV_RETRY_LEAN=0`` → always **False**, even on retry (opt-out
  for operators that want full context on every run).
"""

from __future__ import annotations

import pytest

from backend.App.orchestration.application.nodes.dev import is_dev_retry_lean


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    monkeypatch.delenv("SWARM_DEV_RETRY_LEAN", raising=False)
    yield


def test_first_run_is_not_lean():
    """Clean state — no prior review, no re-prompt marker → full prompt."""
    assert is_dev_retry_lean({}) is False


def test_retry_after_needs_work_is_lean():
    """``dev_review_output`` populated means we're running after a reviewer
    pass — the second Dev call can skip redundant context."""
    state = {"dev_review_output": "## Review\n\nVERDICT: NEEDS_WORK\n\nIssues: …"}
    assert is_dev_retry_lean(state) is True


def test_retry_with_swarm_reprompt_marker_is_lean():
    """Format-enforcement sets ``_swarm_file_reprompt`` to trigger a Dev
    re-run with an explicit wrapping instruction. Treat it like a retry."""
    state = {"_swarm_file_reprompt": "Please wrap code in <swarm_file> tags."}
    assert is_dev_retry_lean(state) is True


def test_both_markers_set_is_lean():
    state = {
        "dev_review_output": "VERDICT: NEEDS_WORK",
        "_swarm_file_reprompt": "Wrap in tags",
    }
    assert is_dev_retry_lean(state) is True


def test_env_opt_out_forces_full_prompt_even_on_retry(monkeypatch):
    """``SWARM_DEV_RETRY_LEAN=0`` brings back the full prompt on retry."""
    monkeypatch.setenv("SWARM_DEV_RETRY_LEAN", "0")
    state = {"dev_review_output": "NEEDS_WORK"}
    assert is_dev_retry_lean(state) is False


@pytest.mark.parametrize("val", ["false", "no", "off", "FALSE", " NO "])
def test_env_opt_out_aliases(monkeypatch, val):
    monkeypatch.setenv("SWARM_DEV_RETRY_LEAN", val)
    state = {"dev_review_output": "NEEDS_WORK"}
    assert is_dev_retry_lean(state) is False


def test_empty_strings_are_not_markers():
    """Empty or whitespace-only values must NOT trigger lean mode — they
    mean the field never got populated by the retry flow."""
    state = {"dev_review_output": "   ", "_swarm_file_reprompt": ""}
    assert is_dev_retry_lean(state) is False

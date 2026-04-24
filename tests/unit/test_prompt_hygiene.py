"""Tests for ``prompt_hygiene`` (§23.4).

The head of every LLM prompt should stay stable call-to-call — volatile
bytes (timestamps, UUIDs, correlation ids) near the start invalidate the
LM Studio / llama.cpp slot prefix cache and turn a sub-second prefill
into a full re-prefill.

``detect_volatile_head`` must:

* detect the common offenders in the first 4 KB;
* report stable labels so regression tests can assert on them;
* return an empty list for clean inputs;
* honour an explicit head-size limit (bytes beyond the limit are
  allowed to contain anything).
"""

from __future__ import annotations

import pytest

from backend.App.orchestration.application.context.prompt_hygiene import (
    DEFAULT_MAX_HEAD_CHARS,
    assert_prompt_head_stable,
    detect_volatile_head,
)


# ---------------------------------------------------------------------------
# Clean inputs — no false positives
# ---------------------------------------------------------------------------


def test_empty_prompt_is_clean():
    assert detect_volatile_head("") == []


def test_plain_english_head_is_clean():
    head = (
        "You are the Product Manager. Read the requirements below and "
        "produce a structured task list with clear acceptance criteria. "
        "Do NOT invent external dependencies the user did not mention."
    )
    assert detect_volatile_head(head) == []


def test_stable_system_prompt_is_clean():
    # Realistic stable prefix — tool schemas, role pitch, style rules.
    head = (
        "SYSTEM: You are a senior backend engineer. Use Python 3.11 idioms. "
        "Tools: {workspace_read_file, workspace_write_file, workspace_list_directory}. "
        "Always cite file paths relative to the project root."
    )
    assert detect_volatile_head(head) == []


# ---------------------------------------------------------------------------
# Each volatile pattern is caught
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "head,expected_label",
    [
        (
            "Run started at 2026-04-16T12:34:56Z. Read tools below.",
            "iso_datetime",
        ),
        (
            "Generated 2026-04-16 12:34:56 — review checklist:",
            "iso_datetime",
        ),
        (
            "Correlation: 00af8687-797e-4caa-96ef-3c0968322386\nTools:",
            "uuid",
        ),
        (
            "task_id: 09c69f48-312f-4df4-91eb-48f093a5fbbe\n",
            "task_id_correlation",
        ),
        (
            "X-Request-Id: req_abc123\nContinue with the step.",
            "request_id_header",
        ),
        (
            "elapsed = 1234.5ms — the model took a while.",
            "elapsed_timing",
        ),
        (
            "nonce = deadbeef1234\nContinue",
            "nonce_like",
        ),
        (
            "timestamp=1729000000 — recent epoch",
            "epoch_seconds",
        ),
    ],
)
def test_each_volatile_pattern_is_detected(head, expected_label):
    hits = detect_volatile_head(head)
    labels = [h.label for h in hits]
    assert expected_label in labels, (
        f"expected {expected_label!r} in {labels!r} for head {head!r}"
    )


# ---------------------------------------------------------------------------
# Boundary behaviour
# ---------------------------------------------------------------------------


def test_volatile_content_beyond_head_is_ignored():
    """Bytes past ``max_head_chars`` can contain anything — caching only
    depends on the head prefix."""
    stable_head = "Stable system prompt. " * 300  # ~7 KB of clean text
    volatile_tail = "\ntask_id: 09c69f48-312f-4df4-91eb-48f093a5fbbe"
    prompt = stable_head + volatile_tail
    # Past 4 KB: nothing detected.
    assert detect_volatile_head(prompt, max_head_chars=DEFAULT_MAX_HEAD_CHARS) == []


def test_volatile_at_the_very_head_is_flagged():
    """The classic regression: timestamp at byte 0."""
    prompt = "2026-04-16T12:34:56Z — session start.\n" + "stable body\n" * 500
    hits = detect_volatile_head(prompt)
    assert hits, "timestamp at offset 0 must be detected"
    assert hits[0].label == "iso_datetime"
    assert hits[0].offset == 0


def test_multiple_matches_are_returned_in_offset_order():
    head = (
        "task_id: 11111111-2222-3333-4444-555555555555\n"
        "Generated at 2026-04-16T10:00:00Z\n"
        "X-Request-Id: r-42\n"
    )
    hits = detect_volatile_head(head)
    offsets = [h.offset for h in hits]
    assert offsets == sorted(offsets)
    labels = {h.label for h in hits}
    assert "task_id_correlation" in labels
    assert "uuid" in labels
    assert "iso_datetime" in labels
    assert "request_id_header" in labels


# ---------------------------------------------------------------------------
# assert_prompt_head_stable — raises with human-readable report
# ---------------------------------------------------------------------------


def test_assert_prompt_head_stable_passes_on_clean_input():
    assert_prompt_head_stable("Plain stable system prompt body.")


def test_assert_prompt_head_stable_raises_with_offending_snippets():
    prompt = "task_id: 12345678-abcd-4def-9012-3456789abcde\nRest of prompt."
    with pytest.raises(AssertionError) as excinfo:
        assert_prompt_head_stable(prompt, context="dev-subtask")
    msg = str(excinfo.value)
    assert "dev-subtask" in msg
    assert "task_id_correlation" in msg or "uuid" in msg

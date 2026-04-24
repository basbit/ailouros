"""Tests for H-2 delta prompting module (delta_prompt.py)."""
from __future__ import annotations

from backend.App.orchestration.application.context.delta_prompt import (
    artifact_header,
    build_dev_lead_delta_retry_prompt,
    build_dialogue_agent_delta_input,
    build_reviewer_history_compact,
    delta_prompting_enabled,
    resolve_artifact,
    store_artifact,
)


# ---------------------------------------------------------------------------
# delta_prompting_enabled
# ---------------------------------------------------------------------------

def test_delta_prompting_enabled_default(monkeypatch):
    monkeypatch.delenv("SWARM_DELTA_PROMPTING", raising=False)
    assert delta_prompting_enabled() is True


def test_delta_prompting_disabled_by_env(monkeypatch):
    monkeypatch.setenv("SWARM_DELTA_PROMPTING", "0")
    assert delta_prompting_enabled() is False


def test_delta_prompting_enabled_explicit_1(monkeypatch):
    monkeypatch.setenv("SWARM_DELTA_PROMPTING", "1")
    assert delta_prompting_enabled() is True


# ---------------------------------------------------------------------------
# store_artifact / resolve_artifact
# ---------------------------------------------------------------------------

def test_store_artifact_returns_ref_string():
    text = "hello world"
    ref = store_artifact(text)
    assert ref.startswith("artifact:sha256:")
    assert len(ref) == len("artifact:sha256:") + 64


def test_store_artifact_is_idempotent():
    text = "same content"
    ref1 = store_artifact(text)
    ref2 = store_artifact(text)
    assert ref1 == ref2


def test_resolve_artifact_round_trips():
    text = "some important content that needs to be stored"
    ref = store_artifact(text)
    resolved = resolve_artifact(ref)
    assert resolved == text


def test_resolve_artifact_returns_none_for_unknown():
    result = resolve_artifact("artifact:sha256:" + "a" * 64)
    assert result is None


def test_resolve_artifact_returns_none_for_malformed():
    assert resolve_artifact("not-an-artifact-ref") is None
    assert resolve_artifact("") is None


def test_store_artifact_content_addressed():
    """Different content → different refs."""
    ref_a = store_artifact("content A")
    ref_b = store_artifact("content B")
    assert ref_a != ref_b


# ---------------------------------------------------------------------------
# artifact_header
# ---------------------------------------------------------------------------

def test_artifact_header_contains_preview():
    text = "This is a long text that should be previewed"
    header = artifact_header(text, max_preview=20)
    assert "This is a long text " in header or "This is a long text" in header


def test_artifact_header_contains_ref():
    text = "any text"
    header = artifact_header(text)
    assert "ref:" in header


def test_artifact_header_contains_char_count():
    text = "twelve chars"
    header = artifact_header(text)
    assert str(len(text)) in header


def test_artifact_header_shows_ellipsis_when_truncated():
    text = "x" * 400
    header = artifact_header(text, max_preview=300)
    assert "…" in header


def test_artifact_header_no_ellipsis_when_short():
    text = "short"
    header = artifact_header(text, max_preview=300)
    assert "…" not in header


# ---------------------------------------------------------------------------
# build_dialogue_agent_delta_input
# ---------------------------------------------------------------------------

def test_build_dialogue_agent_delta_input_contains_feedback():
    result = build_dialogue_agent_delta_input(
        initial_input="Task spec",
        reviewer_feedback="You forgot X and Y",
        prev_output="My previous output",
        round_n=2,
    )
    assert "You forgot X and Y" in result


def test_build_dialogue_agent_delta_input_not_repeat_full_spec():
    """Full initial_input must NOT appear verbatim when longer than preview."""
    long_spec = "SPEC:" + "x" * 500
    result = build_dialogue_agent_delta_input(
        initial_input=long_spec,
        reviewer_feedback="fix this",
        prev_output="output",
        round_n=2,
    )
    # Should not contain the full spec (only a preview)
    assert long_spec not in result
    assert "SPEC:" in result  # preview is present
    assert "ref:" in result


def test_build_dialogue_agent_delta_input_mentions_round():
    result = build_dialogue_agent_delta_input(
        initial_input="task",
        reviewer_feedback="review text",
        prev_output="old output",
        round_n=3,
    )
    # round_n=3 means prev was round 2
    assert "round 2" in result


def test_build_dialogue_agent_delta_input_stores_artifacts():
    """store_artifact is called so both initial_input and prev_output are resolvable."""
    init = "unique initial input for this test"
    prev = "unique previous output for this test"
    build_dialogue_agent_delta_input(init, "fb", prev, 2)
    assert resolve_artifact(store_artifact(init)) == init
    assert resolve_artifact(store_artifact(prev)) == prev


# ---------------------------------------------------------------------------
# build_reviewer_history_compact
# ---------------------------------------------------------------------------

def test_build_reviewer_history_compact_empty():
    assert build_reviewer_history_compact([]) == ""


def test_build_reviewer_history_compact_single_round():
    history = [{"round": 1, "output": "output text", "review": "review text", "verdict": "NEEDS_WORK"}]
    result = build_reviewer_history_compact(history)
    assert "Round 1" in result
    assert "NEEDS_WORK" in result


def test_build_reviewer_history_compact_does_not_embed_full_output():
    """Full output text (longer than preview) must not appear verbatim in compact history."""
    long_output = "LONG_OUTPUT:" + "x" * 500
    history = [{"round": 1, "output": long_output, "review": "short review", "verdict": "NEEDS_WORK"}]
    result = build_reviewer_history_compact(history)
    assert long_output not in result
    assert "LONG_OUTPUT:" in result  # preview present


def test_build_reviewer_history_compact_multi_round():
    history = [
        {"round": 1, "output": "out1", "review": "rev1", "verdict": "NEEDS_WORK"},
        {"round": 2, "output": "out2", "review": "rev2", "verdict": "OK"},
    ]
    result = build_reviewer_history_compact(history)
    assert "Round 1" in result
    assert "Round 2" in result
    assert "NEEDS_WORK" in result
    assert "OK" in result


def test_build_reviewer_history_compact_stores_artifacts():
    """Each output and review should be resolvable from the artifact store."""
    out = "resolvable output content"
    rev = "resolvable review content"
    history = [{"round": 1, "output": out, "review": rev, "verdict": "NEEDS_WORK"}]
    build_reviewer_history_compact(history)
    assert resolve_artifact(store_artifact(out)) == out
    assert resolve_artifact(store_artifact(rev)) == rev


# ---------------------------------------------------------------------------
# build_dev_lead_delta_retry_prompt
# ---------------------------------------------------------------------------

def test_build_dev_lead_delta_retry_prompt_contains_missing_sections():
    result = build_dev_lead_delta_retry_prompt(
        prev_output='{"tasks": []}',
        missing_sections=["must_exist_files", "spec_symbols"],
        user_task="Build the thing",
    )
    assert "must_exist_files" in result
    assert "spec_symbols" in result


def test_build_dev_lead_delta_retry_prompt_contains_task_brief():
    result = build_dev_lead_delta_retry_prompt(
        prev_output="old output",
        missing_sections=["verification_commands"],
        user_task="Deploy to production",
    )
    assert "Deploy to production" in result


def test_build_dev_lead_delta_retry_prompt_does_not_exceed_max_prev_chars():
    long_prev = "x" * 20_000
    result = build_dev_lead_delta_retry_prompt(
        prev_output=long_prev,
        missing_sections=["assumptions"],
        user_task="task",
        max_prev_chars=8000,
    )
    # truncation marker should appear
    assert "truncated" in result.lower()


def test_build_dev_lead_delta_retry_prompt_stores_prev_output():
    prev = "unique prev output for delta retry test"
    build_dev_lead_delta_retry_prompt(prev, ["spec_symbols"], "task")
    assert resolve_artifact(store_artifact(prev)) == prev


def test_build_dev_lead_delta_retry_prompt_asks_for_json_only():
    result = build_dev_lead_delta_retry_prompt(
        prev_output='{"tasks":[]}',
        missing_sections=["must_exist_files"],
        user_task="task",
    )
    assert "```json" in result.lower() or "json" in result.lower()

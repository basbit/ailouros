"""Tests for K-1: Self-Verification Loop."""
from __future__ import annotations

from backend.App.orchestration.application.self_verify import SelfVerifier, VerifyResult, run_with_self_verify


def test_verify_disabled_by_default(monkeypatch):
    """SWARM_SELF_VERIFY=0 (default): verify() returns passed=True without calling LLM."""
    monkeypatch.setenv("SWARM_SELF_VERIFY", "0")
    import backend.App.orchestration.application.self_verify as sv
    monkeypatch.setattr(sv, "_VERIFY_ENABLED", False)
    verifier = SelfVerifier()
    result = verifier.verify(task_spec="spec", output="output")
    assert result.passed is True
    assert result.issues == []


def test_verify_enabled_passes_on_empty_issues(monkeypatch):
    """SWARM_SELF_VERIFY=1, verifier returns [] JSON: passed=True, issues=[]."""
    import backend.App.orchestration.application.self_verify as sv
    monkeypatch.setattr(sv, "_VERIFY_ENABLED", True)
    monkeypatch.setattr(sv.SelfVerifier, "_call_verifier", lambda self, ts, o: VerifyResult(passed=True, issues=[]))
    verifier = SelfVerifier()
    result = verifier.verify(task_spec="spec", output="output")
    assert result.passed is True
    assert result.issues == []


def test_verify_enabled_returns_issues(monkeypatch):
    """SWARM_SELF_VERIFY=1, verifier returns issues: passed=False."""
    import backend.App.orchestration.application.self_verify as sv
    monkeypatch.setattr(sv, "_VERIFY_ENABLED", True)
    monkeypatch.setattr(sv.SelfVerifier, "_call_verifier", lambda self, ts, o: VerifyResult(passed=False, issues=["issue A", "issue B"]))
    verifier = SelfVerifier()
    result = verifier.verify(task_spec="spec", output="output")
    assert result.passed is False
    assert "issue A" in result.issues


def test_verify_exception_returns_not_passed(monkeypatch):
    """LLM call raises exception: returns passed=False with error in issues (§9.2 — no silent swallow)."""
    import backend.App.orchestration.application.self_verify as sv
    monkeypatch.setattr(sv, "_VERIFY_ENABLED", True)

    def _fail(self, ts, o):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(sv.SelfVerifier, "_call_verifier", _fail)
    verifier = SelfVerifier()
    result = verifier.verify(task_spec="spec", output="output")
    assert result.passed is False
    assert any("LLM unavailable" in issue for issue in result.issues)


def test_run_with_self_verify_disabled_calls_once(monkeypatch):
    """Disabled: agent_fn called exactly once, output returned unchanged."""
    import backend.App.orchestration.application.self_verify as sv
    monkeypatch.setattr(sv, "_VERIFY_ENABLED", False)
    call_count = {"n": 0}

    def agent_fn(user_input):
        call_count["n"] += 1
        return "output"

    result = run_with_self_verify(agent_fn, "task spec", "user input")
    assert result == "output"
    assert call_count["n"] == 1


def test_run_with_self_verify_retries_on_issues(monkeypatch):
    """Issues found: agent_fn called twice; second call receives issues in user_input."""
    import backend.App.orchestration.application.self_verify as sv
    monkeypatch.setattr(sv, "_VERIFY_ENABLED", True)
    calls = []

    def agent_fn(user_input):
        calls.append(user_input)
        return "output"

    monkeypatch.setattr(sv.SelfVerifier, "_call_verifier", lambda self, ts, o: VerifyResult(passed=False, issues=["missing X"]))
    run_with_self_verify(agent_fn, "task spec", "original input")
    assert len(calls) == 2
    assert "missing X" in calls[1]  # issues injected into second call


def test_run_with_self_verify_no_retry_on_pass(monkeypatch):
    """passed=True on first attempt: agent_fn called exactly once."""
    import backend.App.orchestration.application.self_verify as sv
    monkeypatch.setattr(sv, "_VERIFY_ENABLED", True)
    call_count = {"n": 0}

    def agent_fn(user_input):
        call_count["n"] += 1
        return "good output"

    monkeypatch.setattr(sv.SelfVerifier, "_call_verifier", lambda self, ts, o: VerifyResult(passed=True, issues=[]))
    run_with_self_verify(agent_fn, "task spec", "input")
    assert call_count["n"] == 1


def test_run_with_self_verify_uses_delta_prompt_on_retry(monkeypatch):
    """H-2: when the original prompt is large and delta prompting is enabled,
    the retry sends a compact delta suffix (task artifact ref + prev output
    ref + issues) instead of blindly re-concatenating the full prompt.

    We don't check exact text — just that the retry prompt grew by the
    delta section *and* contains the issue list + artifact marker.
    """
    import backend.App.orchestration.application.self_verify as sv
    monkeypatch.setattr(sv, "_VERIFY_ENABLED", True)
    monkeypatch.setenv("SWARM_DELTA_PROMPTING", "1")
    monkeypatch.setattr(
        sv.SelfVerifier,
        "_call_verifier",
        lambda self, ts, o: VerifyResult(passed=False, issues=["missing block A", "wrong format B"]),
    )
    calls: list[str] = []

    def agent_fn(user_input):
        calls.append(user_input)
        return "attempt-output"

    # Prompt > 2000 chars triggers the delta path.
    big_prompt = "SYSTEM: do the thing.\n\n" + ("filler context line\n" * 200)
    run_with_self_verify(agent_fn, "the canonical task spec", big_prompt)
    assert len(calls) == 2

    retry_prompt = calls[1]
    # Issues list is still present
    assert "missing block A" in retry_prompt
    assert "wrong format B" in retry_prompt
    # Delta markers are present
    assert "Self-verify retry" in retry_prompt
    assert "ref:" in retry_prompt  # artifact_header embeds ref:<sha>
    # The original prompt is still the prefix (slot-cache friendly).
    assert retry_prompt.startswith(big_prompt)


def test_run_with_self_verify_legacy_path_on_small_prompt(monkeypatch):
    """H-2 delta skipped when the original prompt is already compact
    (<2000 chars) — the legacy ``Previous attempt issues:`` suffix is
    cheaper and preserves backwards-compat for existing callers."""
    import backend.App.orchestration.application.self_verify as sv
    monkeypatch.setattr(sv, "_VERIFY_ENABLED", True)
    monkeypatch.setenv("SWARM_DELTA_PROMPTING", "1")
    monkeypatch.setattr(
        sv.SelfVerifier,
        "_call_verifier",
        lambda self, ts, o: VerifyResult(passed=False, issues=["issue X"]),
    )
    calls: list[str] = []

    def agent_fn(user_input):
        calls.append(user_input)
        return "output"

    run_with_self_verify(agent_fn, "task", "short prompt")
    retry_prompt = calls[1]
    assert "Previous attempt issues:" in retry_prompt
    assert "Self-verify retry" not in retry_prompt


def test_run_with_self_verify_delta_disabled_via_env(monkeypatch):
    """SWARM_DELTA_PROMPTING=0 forces legacy concat even for large prompts."""
    import backend.App.orchestration.application.self_verify as sv
    monkeypatch.setattr(sv, "_VERIFY_ENABLED", True)
    monkeypatch.setenv("SWARM_DELTA_PROMPTING", "0")
    monkeypatch.setattr(
        sv.SelfVerifier,
        "_call_verifier",
        lambda self, ts, o: VerifyResult(passed=False, issues=["issue Y"]),
    )
    calls: list[str] = []

    def agent_fn(user_input):
        calls.append(user_input)
        return "output"

    big_prompt = "x" * 5000
    run_with_self_verify(agent_fn, "task", big_prompt)
    retry_prompt = calls[1]
    assert "Previous attempt issues:" in retry_prompt
    assert "Self-verify retry" not in retry_prompt

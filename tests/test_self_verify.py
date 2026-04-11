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

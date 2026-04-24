from __future__ import annotations

from backend.App.orchestration.application.nodes.pm_clarify import (
    _clarify_cache_identity,
    _clarify_cache_key,
    _load_clarify_cache,
    _clarify_requires_fresh_research,
    _save_clarify_cache,
    clarify_input_node,
)


def test_clarify_cache_key_differs_for_workspace_identity_changes() -> None:
    state_a = {
        "workspace_root": "/repo/a",
        "project_manifest": "manifest-a",
        "workspace_snapshot": "snapshot-a",
    }
    state_b = {
        "workspace_root": "/repo/b",
        "project_manifest": "manifest-a",
        "workspace_snapshot": "snapshot-a",
    }

    identity_a = _clarify_cache_identity(state_a, "same task")
    identity_b = _clarify_cache_identity(state_b, "same task")

    assert identity_a != identity_b
    assert _clarify_cache_key(identity_a) != _clarify_cache_key(identity_b)


def test_load_clarify_cache_rejects_workspace_identity_mismatch(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SWARM_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    cache_state = {
        "workspace_root": "/repo/a",
        "project_manifest": "manifest-a",
        "workspace_snapshot": "snapshot-a",
    }
    request_state = {
        "workspace_root": "/repo/b",
        "project_manifest": "manifest-a",
        "workspace_snapshot": "snapshot-a",
    }
    cache_identity = _clarify_cache_identity(cache_state, "same task")
    request_identity = _clarify_cache_identity(request_state, "same task")
    cache_key = _clarify_cache_key(cache_identity)

    _save_clarify_cache(cache_key, cache_identity, "cached answer")

    loaded = _load_clarify_cache(cache_key, request_identity, force_rerun=False)

    assert loaded is None


def test_clarify_input_node_uses_exact_cache_identity(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SWARM_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    state = {
        "input": "Need a plan",
        "workspace_root": str(tmp_path / "workspace"),
        "project_manifest": "manifest-a",
        "workspace_snapshot": "snapshot-a",
        "agent_config": {},
    }
    identity = _clarify_cache_identity(state, state["input"])
    cache_key = _clarify_cache_key(identity)
    _save_clarify_cache(cache_key, identity, "READY: cached clarify result")

    result = clarify_input_node(dict(state))

    assert result["clarify_input_model"] == "cache"
    assert result["clarify_input_provider"] == "cache"
    assert "result from cache of previous run" in result["clarify_input_output"]
    assert result["clarify_input_cache"]["hit"] is True
    assert result["clarify_input_cache"]["identity"]["workspace_root"] == state["workspace_root"]


def test_clarify_requires_fresh_research_detects_web_intent() -> None:
    state = {"agent_config": {}}
    assert _clarify_requires_fresh_research(state, "Поищи в интернете актуальные сайты событий") is True


def test_clarify_input_node_skips_cache_for_fresh_research(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SWARM_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    state = {
        "input": "Search the web for current Cyprus events sites",
        "workspace_root": str(tmp_path / "workspace"),
        "project_manifest": "manifest-a",
        "workspace_snapshot": "snapshot-a",
        "agent_config": {},
    }
    identity = _clarify_cache_identity(state, state["input"])
    cache_key = _clarify_cache_key(identity)
    _save_clarify_cache(cache_key, identity, "READY: cached clarify result")

    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.pm_clarify._llm_planning_agent_run",
        lambda agent, prompt, current_state, **kwargs: ("READY: fresh clarify result", "m", "p"),
    )
    monkeypatch.setattr(
        "backend.App.orchestration.application.nodes.pm_clarify.ReviewerAgent",
        lambda *args, **kwargs: type("Fake", (), {"used_model": "m", "used_provider": "p"})(),
    )

    result = clarify_input_node(dict(state))

    assert result["clarify_input_model"] == "m"
    assert result["clarify_input_cache"]["hit"] is False
    assert result["clarify_input_cache"]["reuse_blocked_reason"] == "fresh_external_research_required"

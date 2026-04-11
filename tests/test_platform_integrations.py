"""Реестр, пресеты, cross-task memory (in-process), MoA выключен по умолчанию."""

from __future__ import annotations

import json

from backend.App.integrations.infrastructure.agent_registry import merge_agent_config
from backend.App.integrations.infrastructure.cross_task_memory import (
    append_episode,
    cross_task_memory_enabled,
    format_cross_task_memory_block,
    search_episodes,
)
from backend.App.integrations.infrastructure.pipeline_presets import resolve_preset
from backend.App.orchestration.application.review_moa import moa_enabled_for_step


def test_merge_agent_config_registry_defaults(tmp_path, monkeypatch):
    reg = {
        "defaults": {"reviewer": {"prompt_path": "specialized/specialized-reviewer.md"}},
        "roles": {"pm": {"enabled": True, "config": {"model": "x-test-model"}}},
    }
    p = tmp_path / "reg.json"
    p.write_text(json.dumps(reg), encoding="utf-8")
    monkeypatch.setenv("SWARM_AGENT_REGISTRY_PATH", str(p))
    from backend.App.integrations.infrastructure import agent_registry as ar

    ar._CACHE = (0.0, {})  # type: ignore[misc]

    out = merge_agent_config({"dev": {"model": "dev-only"}})
    assert out["reviewer"]["prompt_path"] == "specialized/specialized-reviewer.md"
    assert out["pm"]["model"] == "x-test-model"
    assert out["dev"]["model"] == "dev-only"


def test_resolve_preset_planning_loop():
    steps = resolve_preset("planning_loop")
    assert steps is not None
    assert steps[0] == "pm"
    assert "human_spec" in steps


def test_cross_task_memory_local_roundtrip(monkeypatch):
    monkeypatch.setattr(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        lambda: None,
    )
    state = {
        "agent_config": {
            "swarm": {
                "cross_task_memory": {
                    "enabled": True,
                    "namespace": "pytest_ns",
                    "persist_steps": ["human_spec"],
                }
            }
        },
        "task_id": "t1",
    }
    assert cross_task_memory_enabled(state)
    append_episode(state, step_id="human_spec", body="jwt refresh token rotation", task_id="t1")
    hits = search_episodes(state, "jwt token", limit=3)
    assert hits


def test_cross_task_memory_inject_block(monkeypatch):
    monkeypatch.setattr(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        lambda: None,
    )
    state = {
        "agent_config": {
            "swarm": {
                "cross_task_memory": {
                    "enabled": True,
                    "namespace": "pytest_inj",
                    "inject_at_steps": ["pm", "ba"],
                }
            }
        },
        "input": "login flow",
    }
    append_episode(state, step_id="human_qa", body="use oauth2 pkce for login", task_id="x")
    b = format_cross_task_memory_block(state, "oauth login", current_step="pm")
    assert "pkce" in b.lower() or "oauth" in b.lower()


def test_moa_disabled_by_default():
    state = {"agent_config": {"reviewer": {}}}
    assert not moa_enabled_for_step(state, "review_spec")


def test_moa_enabled_when_configured():
    state = {
        "agent_config": {
            "reviewer": {"moa": {"enabled": True, "steps": ["review_spec"], "panel_size": 2}}
        }
    }
    assert moa_enabled_for_step(state, "review_spec")
    assert not moa_enabled_for_step(state, "review_pm")

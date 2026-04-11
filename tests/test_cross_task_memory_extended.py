"""Extended tests for cross_task_memory.py — persist_after_pipeline_step."""
from __future__ import annotations

from unittest.mock import patch

from backend.App.integrations.infrastructure.cross_task_memory import (
    persist_after_pipeline_step,
)

# Redis may be installed in the dev environment; patch it out so tests use _LOCAL_EPISODES
_NO_REDIS = patch("backend.App.integrations.infrastructure.cross_task_memory._redis", return_value=None)


def _state_with_memory(persist_steps=None, **extra):
    mem_cfg = {"enabled": True}
    if persist_steps is not None:
        mem_cfg["persist_steps"] = persist_steps
    state = {
        "agent_config": {
            "swarm": {
                "cross_task_memory": mem_cfg,
            }
        }
    }
    state.update(extra)
    return state


# ---------------------------------------------------------------------------
# persist_after_pipeline_step
# ---------------------------------------------------------------------------

def test_persist_after_pipeline_step_disabled():
    """Returns early when memory is disabled."""
    state = {}  # no cross_task_memory config → disabled
    persist_after_pipeline_step("dev", state, {"dev_output": "some code"})


def test_persist_after_pipeline_step_no_persist_steps():
    state = _state_with_memory()  # no persist_steps key
    persist_after_pipeline_step("dev", state, {"dev_output": "code"})
    # Should return early — no crash


def test_persist_after_pipeline_step_step_not_in_allowed():
    state = _state_with_memory(persist_steps=["pm"])
    # "dev" not in allowed → early return
    persist_after_pipeline_step("dev", state, {"dev_output": "code"})


def test_persist_after_pipeline_step_allowed_step():
    """When step is allowed and output exists, episode is stored."""
    import backend.App.integrations.infrastructure.cross_task_memory as ctm
    ctm._LOCAL_EPISODES.clear()

    state = _state_with_memory(
        persist_steps=["pm"],
        task_id="test-task-123",
        pm_output="PM output text",
        pm_memory_artifact={
            "facts": [],
            "hypotheses": [],
            "decisions": ["Split checkout and auth into separate subtasks"],
            "dead_ends": [],
            "constraints": ["Keep the payment flow unchanged"],
        },
    )
    delta = {"pm_output": "PM output text"}
    with _NO_REDIS:
        persist_after_pipeline_step("pm", state, delta)
    ns = ctm.memory_namespace(state)
    episodes = ctm._LOCAL_EPISODES.get(ns, [])
    assert len(episodes) >= 1
    assert episodes[0]["step"] == "pm"
    ctm._LOCAL_EPISODES.clear()


def test_persist_after_pipeline_step_empty_output():
    """Empty output → no episode stored."""
    import backend.App.integrations.infrastructure.cross_task_memory as ctm
    ctm._LOCAL_EPISODES.clear()

    state = _state_with_memory(persist_steps=["pm"], pm_output="")
    delta = {"pm_output": ""}
    persist_after_pipeline_step("pm", state, delta)
    ns = ctm.memory_namespace(state)
    episodes = ctm._LOCAL_EPISODES.get(ns, [])
    assert len(episodes) == 0
    ctm._LOCAL_EPISODES.clear()


def test_persist_after_pipeline_step_crole_output():
    """Custom role output without explicit memory artifact is skipped."""
    import backend.App.integrations.infrastructure.cross_task_memory as ctm
    ctm._LOCAL_EPISODES.clear()

    state = _state_with_memory(
        persist_steps=["crole_myagent"],
        crole_myagent_output="custom output text",
    )
    delta = {"crole_myagent_output": "custom output text"}
    with _NO_REDIS:
        persist_after_pipeline_step("crole_myagent", state, delta)
    ns = ctm.memory_namespace(state)
    assert ctm._LOCAL_EPISODES.get(ns, []) == []
    ctm._LOCAL_EPISODES.clear()


def test_persist_after_pipeline_step_crole_output_with_memory_artifact():
    import backend.App.integrations.infrastructure.cross_task_memory as ctm
    ctm._LOCAL_EPISODES.clear()

    state = _state_with_memory(
        persist_steps=["crole_myagent"],
        crole_myagent_output="custom output text",
        crole_myagent_memory_artifact={
            "facts": [],
            "hypotheses": [],
            "decisions": ["Generate onboarding docs for the current API"],
            "dead_ends": [],
            "constraints": ["Do not invent endpoints missing from the repository"],
        },
    )
    delta = {"crole_myagent_output": "custom output text"}
    with _NO_REDIS:
        persist_after_pipeline_step("crole_myagent", state, delta)
    ns = ctm.memory_namespace(state)
    episodes = ctm._LOCAL_EPISODES.get(ns, [])
    assert len(episodes) == 1
    assert episodes[0]["decisions"] == ["Generate onboarding docs for the current API"]
    ctm._LOCAL_EPISODES.clear()


def test_persist_after_pipeline_step_unknown_step():
    """Unknown step not in ARTIFACT_AGENT_OUTPUT_KEYS and not crole_ → no episode."""
    import backend.App.integrations.infrastructure.cross_task_memory as ctm
    ctm._LOCAL_EPISODES.clear()

    state = _state_with_memory(
        persist_steps=["mystery_step"],
        mystery_step_output="mystery output",
    )
    delta = {"mystery_step_output": "mystery output"}
    persist_after_pipeline_step("mystery_step", state, delta)
    ns = ctm.memory_namespace(state)
    episodes = ctm._LOCAL_EPISODES.get(ns, [])
    assert len(episodes) == 0
    ctm._LOCAL_EPISODES.clear()


def test_persist_after_pipeline_step_uses_delta_first():
    """delta is checked for output before state."""
    import backend.App.integrations.infrastructure.cross_task_memory as ctm
    ctm._LOCAL_EPISODES.clear()

    state = _state_with_memory(
        persist_steps=["pm"],
        pm_output="state output",
        pm_memory_artifact={
            "facts": [],
            "hypotheses": [],
            "decisions": ["Use delta-derived PM artifact"],
            "dead_ends": [],
            "constraints": [],
        },
    )
    delta = {
        "pm_output": "delta output",
        "pm_memory_artifact": {
            "facts": [],
            "hypotheses": [],
            "decisions": ["Use delta-derived PM artifact"],
            "dead_ends": [],
            "constraints": [],
        },
    }
    with _NO_REDIS:
        persist_after_pipeline_step("pm", state, delta)
    ns = ctm.memory_namespace(state)
    episodes = ctm._LOCAL_EPISODES.get(ns, [])
    assert len(episodes) >= 1
    assert episodes[0]["decisions"] == ["Use delta-derived PM artifact"]
    ctm._LOCAL_EPISODES.clear()


def test_persist_after_pipeline_step_uses_state_when_not_in_delta():
    """When delta lacks key, falls back to state."""
    import backend.App.integrations.infrastructure.cross_task_memory as ctm
    ctm._LOCAL_EPISODES.clear()

    state = _state_with_memory(
        persist_steps=["pm"],
        pm_output="state-only output",
        pm_memory_artifact={
            "facts": [],
            "hypotheses": [],
            "decisions": ["Use state-only PM artifact"],
            "dead_ends": [],
            "constraints": [],
        },
    )
    delta = {}  # no pm_output in delta
    with _NO_REDIS:
        persist_after_pipeline_step("pm", state, delta)
    ns = ctm.memory_namespace(state)
    episodes = ctm._LOCAL_EPISODES.get(ns, [])
    assert len(episodes) >= 1
    assert episodes[0]["decisions"] == ["Use state-only PM artifact"]
    ctm._LOCAL_EPISODES.clear()


def test_persist_after_pipeline_step_requires_canonical_memory_artifact_for_pm():
    import backend.App.integrations.infrastructure.cross_task_memory as ctm
    ctm._LOCAL_EPISODES.clear()

    state = _state_with_memory(
        persist_steps=["pm"],
        pm_output="state-only output",
    )
    delta = {"pm_output": "delta output"}
    with _NO_REDIS:
        persist_after_pipeline_step("pm", state, delta)
    ns = ctm.memory_namespace(state)
    assert ctm._LOCAL_EPISODES.get(ns, []) == []


def test_persist_after_pipeline_step_uses_memory_artifact_for_pm():
    import backend.App.integrations.infrastructure.cross_task_memory as ctm
    ctm._LOCAL_EPISODES.clear()

    state = _state_with_memory(
        persist_steps=["pm"],
        pm_output="delta output",
        pm_memory_artifact={
            "facts": [],
            "hypotheses": [],
            "decisions": ["Split auth and billing into separate tasks"],
            "dead_ends": [],
            "constraints": ["Preserve the existing REST API contract"],
        },
    )
    with _NO_REDIS:
        persist_after_pipeline_step("pm", state, {})
    ns = ctm.memory_namespace(state)
    episodes = ctm._LOCAL_EPISODES.get(ns, [])
    assert len(episodes) == 1
    assert episodes[0]["decisions"] == ["Split auth and billing into separate tasks"]
    assert episodes[0]["constraints"] == ["Preserve the existing REST API contract"]
    ctm._LOCAL_EPISODES.clear()

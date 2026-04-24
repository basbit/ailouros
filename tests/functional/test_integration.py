"""Integration tests: full pipeline flow with mocked LLM calls."""

from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Include VERDICT: OK so the reviewer format gate passes and planning gate does not block.
_MOCK_LLM_RETURN = (
    "VERDICT: OK\ntest output has been reviewed and approved.",
    {"input_tokens": 10, "output_tokens": 20, "model": "test", "cached": False},
)

# Patch where it's imported and used in BaseAgent.run()
_ASK_MODEL_PATH = "backend.App.orchestration.infrastructure.agents.base_agent.ask_model"


def _mock_ask_model(*args, **kwargs):
    return _MOCK_LLM_RETURN


# ---------------------------------------------------------------------------
# test_full_pipeline_run
# ---------------------------------------------------------------------------

def test_full_pipeline_run():
    """Run run_pipeline_stream() with all LLM calls mocked; assert final event status=completed."""
    from backend.App.orchestration.application.routing.pipeline_graph import run_pipeline_stream

    # Use a minimal step set to keep the test fast: pm + review_pm only
    steps = ["pm", "review_pm"]

    with patch(_ASK_MODEL_PATH, side_effect=_mock_ask_model):
        events = list(run_pipeline_stream("build a website", pipeline_steps=steps))

    # Each step should yield in_progress + completed (plus optional warning events)
    statuses = [e["status"] for e in events]
    assert "in_progress" in statuses
    assert "completed" in statuses

    # Last event must be completed
    assert events[-1]["status"] == "completed"

    # Agent names should appear in events
    agent_names = {e["agent"] for e in events}
    assert "pm" in agent_names
    assert "review_pm" in agent_names

    # Completed events should carry the mocked output text
    completed = [e for e in events if e["status"] == "completed"]
    for ev in completed:
        assert "VERDICT: OK" in ev["message"]


# ---------------------------------------------------------------------------
# test_pipeline_with_workspace
# ---------------------------------------------------------------------------

def test_pipeline_with_workspace(tmp_path):
    """Mock workspace snapshot and run pipeline; assert workspace_root is in state."""
    from backend.App.orchestration.application.routing.pipeline_graph import run_pipeline_stream

    # Create a minimal workspace directory
    ws = tmp_path / "project"
    ws.mkdir()
    (ws / "main.py").write_text("print('hello')\n", encoding="utf-8")

    steps = ["pm"]

    with patch(_ASK_MODEL_PATH, side_effect=_mock_ask_model):
        events = list(
            run_pipeline_stream(
                "fix a bug",
                pipeline_steps=steps,
                workspace_root=str(ws),
            )
        )

    # completed event: message should be the mocked output (warning events may also be present)
    completed_events = [e for e in events if e["status"] == "completed"]
    assert completed_events, "Expected at least one completed event"
    assert "VERDICT: OK" in completed_events[-1]["message"]


# ---------------------------------------------------------------------------
# test_human_gate_flow
# ---------------------------------------------------------------------------

def test_human_gate_flow():
    """Pipeline with require_manual=True should raise HumanApprovalRequired at a human step."""
    from backend.App.orchestration.application.routing.pipeline_graph import run_pipeline_stream, run_pipeline_stream_resume
    from backend.App.orchestration.domain.exceptions import HumanApprovalRequired

    agent_config = {
        "human": {
            "require_manual": True,
        }
    }
    # Steps: pm → review_pm → human_pm; human step should raise
    steps = ["pm", "review_pm", "human_pm"]

    partial_state = None
    resume_step = None

    with patch(_ASK_MODEL_PATH, side_effect=_mock_ask_model):
        try:
            # Consume the generator; HumanApprovalRequired is raised inside
            for _ in run_pipeline_stream("task", pipeline_steps=steps, agent_config=agent_config):
                pass
            pytest.fail("Expected HumanApprovalRequired to be raised")
        except HumanApprovalRequired as exc:
            assert exc.step == "pm"  # raised at the human_pm step for step "pm"
            partial_state = exc.partial_state
            resume_step = exc.resume_pipeline_step

    assert partial_state is not None
    assert resume_step == "human_pm"

    # Now resume after the human step
    with patch(_ASK_MODEL_PATH, side_effect=_mock_ask_model):
        resume_events = list(
            run_pipeline_stream_resume(
                partial_state=partial_state,
                pipeline_steps=steps,
                resume_from_step=resume_step,
                human_feedback_text="looks good",
            )
        )

    # After resume there are no more steps after human_pm, so no events
    assert isinstance(resume_events, list)
    # All events (if any) should be completed
    for ev in resume_events:
        assert ev["status"] in ("in_progress", "completed")


# ---------------------------------------------------------------------------
# test_task_store_ttl
# ---------------------------------------------------------------------------

def test_task_store_ttl():
    """Create a task in RedisTaskStore with a mocked Redis; verify TTL is set on the key."""
    from backend.App.tasks.infrastructure.task_store_redis import RedisTaskStore, _TASK_TTL_SECONDS

    mock_redis_client = MagicMock()
    mock_redis_client.get.return_value = None

    store = RedisTaskStore(client=mock_redis_client, ttl_sec=_TASK_TTL_SECONDS)

    # The store should hold our mock client
    assert store.client is mock_redis_client

    store.create_task("test prompt")

    # redis set should have been called with `ex=_TASK_TTL_SECONDS`
    assert mock_redis_client.set.called
    call_kwargs = mock_redis_client.set.call_args
    # The `ex` keyword argument should equal the TTL
    ttl_used = call_kwargs.kwargs.get("ex") or call_kwargs[1].get("ex")
    assert ttl_used == _TASK_TTL_SECONDS


# ---------------------------------------------------------------------------
# test_workspace_snapshot_no_path_traversal
# ---------------------------------------------------------------------------

def test_workspace_snapshot_no_path_traversal(tmp_path):
    """safe_relative_path() must raise ValueError for path-traversal inputs."""
    from backend.App.workspace.infrastructure.patch_parser import safe_relative_path

    root = tmp_path / "workspace"
    root.mkdir()

    with pytest.raises(ValueError):
        safe_relative_path(root, "../../etc/passwd")

    with pytest.raises(ValueError):
        safe_relative_path(root, "../secret.txt")


# ---------------------------------------------------------------------------
# test_workspace_env_whitelist
# ---------------------------------------------------------------------------

def test_workspace_env_whitelist():
    """_safe_subprocess_env() must NOT expose ANTHROPIC_API_KEY or OPENAI_API_KEY."""
    # Inject sensitive keys into the environment
    sensitive_env = {
        "ANTHROPIC_API_KEY": "sk-ant-secret",
        "OPENAI_API_KEY": "sk-oai-secret",
        "PATH": "/usr/bin:/bin",  # should be allowed through
    }
    with patch.dict(os.environ, sensitive_env, clear=False):
        from backend.App.workspace.infrastructure import workspace_io
        safe_env = workspace_io._safe_subprocess_env()

    assert "ANTHROPIC_API_KEY" not in safe_env
    assert "OPENAI_API_KEY" not in safe_env
    # PATH should be present (it's in the allowlist)
    assert "PATH" in safe_env

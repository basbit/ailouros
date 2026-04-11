"""Extended tests for backend/App/orchestration/application/tasks.py."""
from unittest.mock import MagicMock, patch

import pytest

import warnings

from backend.App.orchestration.domain.exceptions import HumanApprovalRequired
from backend.App.orchestration.application.tasks import (
    pipeline_workspace_parts_from_meta,
    prepare_workspace,
    resolve_chat_request,
    start_pipeline_run,
)

# start_pipeline_run is deprecated but we still test it for backward compat
warnings.filterwarnings("ignore", message="start_pipeline_run.*deprecated")


# ---------------------------------------------------------------------------
# resolve_chat_request
# ---------------------------------------------------------------------------

def test_resolve_chat_request_basic():
    req = MagicMock()
    req.agent_config = {}
    req.pipeline_steps = ["pm", "ba"]
    req.pipeline_preset = None

    with patch(
        "backend.App.orchestration.application.tasks.merge_agent_config",
        return_value={"merged": True},
    ), patch(
        "backend.App.orchestration.application.tasks.resolve_preset",
        return_value=["x"],
    ):
        agent_config, steps = resolve_chat_request(req)

    assert agent_config == {"merged": True}
    assert steps == ["pm", "ba"]


def test_resolve_chat_request_uses_preset_when_steps_none():
    req = MagicMock()
    req.agent_config = {}
    req.pipeline_steps = None
    req.pipeline_preset = "default"

    with patch(
        "backend.App.orchestration.application.tasks.merge_agent_config",
        return_value={},
    ), patch(
        "backend.App.orchestration.application.tasks.resolve_preset",
        return_value=["pm", "ba", "dev"],
    ) as mock_preset:
        agent_config, steps = resolve_chat_request(req)

    mock_preset.assert_called_once_with("default")
    assert steps == ["pm", "ba", "dev"]


def test_resolve_chat_request_no_preset_no_steps():
    req = MagicMock()
    req.agent_config = {}
    req.pipeline_steps = None
    req.pipeline_preset = None

    with patch(
        "backend.App.orchestration.application.tasks.merge_agent_config",
        return_value={},
    ):
        agent_config, steps = resolve_chat_request(req)

    assert steps is None


# ---------------------------------------------------------------------------
# pipeline_workspace_parts_from_meta
# ---------------------------------------------------------------------------

def test_pipeline_workspace_parts_from_meta_full():
    meta = {
        "user_task": "implement feature X",
        "project_manifest": "manifest text",
        "workspace_snapshot": "snapshot",
        "workspace_context_mode": "full",
        "workspace_section_title": "Workspace",
        "workspace_context_mcp_fallback": True,
    }
    result = pipeline_workspace_parts_from_meta(meta)
    assert result["user_task"] == "implement feature X"
    assert result["project_manifest"] == "manifest text"
    assert result["workspace_snapshot"] == "snapshot"
    assert result["workspace_context_mode"] == "full"
    assert result["workspace_section_title"] == "Workspace"
    assert result["workspace_context_mcp_fallback"] is True


def test_pipeline_workspace_parts_from_meta_defaults():
    result = pipeline_workspace_parts_from_meta({})
    assert result["user_task"] == ""
    assert result["project_manifest"] == ""
    assert result["workspace_context_mcp_fallback"] is False
    assert isinstance(result["workspace_context_mode"], str)


# ---------------------------------------------------------------------------
# prepare_workspace
# ---------------------------------------------------------------------------

def test_prepare_workspace_no_root():
    with patch(
        "backend.App.orchestration.application.tasks.resolve_workspace_context_mode",
        return_value="full",
    ), patch(
        "backend.App.orchestration.application.tasks.build_input_with_workspace",
        return_value="assembled prompt",
    ):
        effective, path, meta = prepare_workspace(
            "do something", None, False
        )

    assert effective == "assembled prompt"
    assert path is None
    assert meta["user_task"] == "do something"


def test_prepare_workspace_workspace_write_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARM_ALLOW_WORKSPACE_WRITE", raising=False)

    with patch(
        "backend.App.orchestration.application.tasks.resolve_workspace_context_mode",
        return_value="full",
    ), patch(
        "backend.App.orchestration.application.tasks.workspace_write_allowed",
        return_value=False,
    ):
        with pytest.raises(ValueError, match="SWARM_ALLOW_WORKSPACE_WRITE"):
            prepare_workspace("task", str(tmp_path), workspace_write=True)


def test_prepare_workspace_with_project_context_file(tmp_path):
    ctx_file = tmp_path / "context.md"
    ctx_file.write_text("project context data")

    with patch(
        "backend.App.orchestration.application.tasks.resolve_workspace_context_mode",
        return_value="full",
    ), patch(
        "backend.App.orchestration.application.tasks.workspace_write_allowed",
        return_value=True,
    ), patch(
        "backend.App.orchestration.application.tasks.validate_workspace_root",
        return_value=tmp_path.resolve(),
    ), patch(
        "backend.App.orchestration.application.tasks.resolve_project_context_path",
        return_value=ctx_file,
    ), patch(
        "backend.App.orchestration.application.tasks.validate_readable_file",
        return_value=ctx_file,
    ), patch(
        "backend.App.orchestration.application.tasks.read_project_context_file",
        return_value="project context data",
    ), patch(
        "backend.App.orchestration.application.tasks.collect_workspace_snapshot",
        return_value=("snapshot text", 5),
    ), patch(
        "backend.App.orchestration.application.tasks.build_input_with_workspace",
        return_value="assembled",
    ):
        effective, path, meta = prepare_workspace(
            "task", str(tmp_path), False,
            project_context_file=str(ctx_file),
        )

    assert meta["project_manifest"] == "project context data"
    assert "project_context_chars" in meta


def test_prepare_workspace_tools_only_no_root():
    with patch(
        "backend.App.orchestration.application.tasks.resolve_workspace_context_mode",
        return_value="tools_only",
    ), patch(
        "backend.App.orchestration.application.tasks.tools_only_workspace_placeholder",
        return_value="placeholder text",
    ), patch(
        "backend.App.orchestration.application.tasks.build_input_with_workspace",
        return_value="assembled",
    ):
        effective, path, meta = prepare_workspace("task", None, False)

    assert path is None
    assert meta["workspace_snapshot"] == "placeholder text"


def test_prepare_workspace_index_only_mode(tmp_path):
    with patch(
        "backend.App.orchestration.application.tasks.resolve_workspace_context_mode",
        return_value="index_only",
    ), patch(
        "backend.App.orchestration.application.tasks.workspace_write_allowed",
        return_value=True,
    ), patch(
        "backend.App.orchestration.application.tasks.validate_workspace_root",
        return_value=tmp_path.resolve(),
    ), patch(
        "backend.App.orchestration.application.tasks.collect_workspace_file_index",
        return_value=("file index text", 10),
    ), patch(
        "backend.App.orchestration.application.tasks.build_input_with_workspace",
        return_value="assembled",
    ):
        effective, path, meta = prepare_workspace("task", str(tmp_path), False)

    assert meta["workspace_section_title"] == "Workspace index"
    assert meta["workspace_snapshot_files"] == 10


# ---------------------------------------------------------------------------
# start_pipeline_run
# ---------------------------------------------------------------------------

def test_start_pipeline_run_success(tmp_path):
    mock_task_store = MagicMock()
    mock_result = {
        "pm_output": "pm result",
        "dev_output": "dev result",
    }

    with patch(
        "backend.App.integrations.infrastructure.observability.logging_config.set_task_id",
    ), patch(
        "backend.App.orchestration.application.tasks.run_pipeline",
        return_value=mock_result,
    ), patch(
        "backend.App.orchestration.application.tasks.final_pipeline_user_message",
        return_value="final message",
    ), patch(
        "backend.App.orchestration.application.tasks.task_store_agent_label",
        return_value="dev",
    ), patch(
        "backend.App.orchestration.application.tasks.workspace_write_allowed",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.tasks.ARTIFACT_AGENT_OUTPUT_KEYS",
        [("pm", "pm_output"), ("dev", "dev_output")],
    ), patch(
        "backend.App.orchestration.application.tasks.append_task_run_log",
    ):
        result = start_pipeline_run(
            user_prompt="do X",
            effective_prompt="full prompt",
            agent_config={},
            steps=["pm", "dev"],
            workspace_root_str="",
            workspace_apply_writes=False,
            workspace_path=None,
            workspace_meta={"workspace_context_mode": "full"},
            task_id="test-task-123",
            task_store=mock_task_store,
            artifacts_root=tmp_path,
            pipeline_snapshot_for_disk=lambda s: s,
            workspace_followup_lines=lambda *a: [],
        )

    assert result["status"] == "completed"
    assert result["task_id"] == "test-task-123"
    assert result["final_text"] == "final message"
    assert result["last_agent"] == "dev"
    mock_task_store.update_task.assert_called()


def test_start_pipeline_run_human_approval(tmp_path):
    mock_task_store = MagicMock()
    exc = HumanApprovalRequired("human_spec", "needs approval")
    exc.partial_state = {"pm_output": "done"}
    exc.resume_pipeline_step = "human_spec"

    with patch(
        "backend.App.integrations.infrastructure.observability.logging_config.set_task_id",
    ), patch(
        "backend.App.orchestration.application.tasks.run_pipeline",
        side_effect=exc,
    ):
        result = start_pipeline_run(
            user_prompt="do X",
            effective_prompt="full prompt",
            agent_config={},
            steps=["pm", "human_spec", "dev"],
            workspace_root_str="",
            workspace_apply_writes=False,
            workspace_path=None,
            workspace_meta={},
            task_id="task-human",
            task_store=mock_task_store,
            artifacts_root=tmp_path,
            pipeline_snapshot_for_disk=lambda s: s,
            workspace_followup_lines=lambda *a: [],
        )

    assert result["status"] == "awaiting_human"
    assert result["human_approval_step"] == "human_spec"
    mock_task_store.update_task.assert_called()


def test_start_pipeline_run_exception(tmp_path):
    mock_task_store = MagicMock()

    with patch(
        "backend.App.integrations.infrastructure.observability.logging_config.set_task_id",
    ), patch(
        "backend.App.orchestration.application.tasks.run_pipeline",
        side_effect=RuntimeError("pipeline crashed"),
    ):
        result = start_pipeline_run(
            user_prompt="do X",
            effective_prompt="full prompt",
            agent_config={},
            steps=None,
            workspace_root_str="",
            workspace_apply_writes=False,
            workspace_path=None,
            workspace_meta={},
            task_id="task-fail",
            task_store=mock_task_store,
            artifacts_root=tmp_path,
            pipeline_snapshot_for_disk=lambda s: s,
            workspace_followup_lines=lambda *a: [],
        )

    assert result["status"] == "failed"
    assert "pipeline crashed" in result["error"]
    assert result["exc_type"] == "RuntimeError"
    mock_task_store.update_task.assert_called()


def test_prepare_workspace_retrieve_mode_with_mcp_servers(tmp_path):
    """RETRIEVE mode when MCP servers are available → tools_only placeholder."""
    with patch(
        "backend.App.orchestration.application.tasks.resolve_workspace_context_mode",
        return_value="retrieve",
    ), patch(
        "backend.App.orchestration.application.tasks.workspace_write_allowed",
        return_value=True,
    ), patch(
        "backend.App.orchestration.application.tasks.validate_workspace_root",
        return_value=tmp_path.resolve(),
    ), patch(
        "backend.App.orchestration.application.tasks.apply_auto_mcp_to_agent_config",
        return_value={"mcp": {"servers": [{"name": "ws"}]}},
    ), patch(
        "backend.App.orchestration.application.tasks.tools_only_workspace_placeholder",
        return_value="mcp placeholder",
    ), patch(
        "backend.App.orchestration.application.tasks.build_input_with_workspace",
        return_value="assembled",
    ):
        effective, path, meta = prepare_workspace(
            "task", str(tmp_path), False, agent_config={}
        )

    assert meta["workspace_snapshot"] == "mcp placeholder"
    assert meta["workspace_context_mcp_fallback"] is False


def test_prepare_workspace_retrieve_mode_no_mcp_fallback_to_index(tmp_path):
    """RETRIEVE mode when MCP unavailable → falls back to file index."""
    with patch(
        "backend.App.orchestration.application.tasks.resolve_workspace_context_mode",
        return_value="retrieve",
    ), patch(
        "backend.App.orchestration.application.tasks.workspace_write_allowed",
        return_value=True,
    ), patch(
        "backend.App.orchestration.application.tasks.validate_workspace_root",
        return_value=tmp_path.resolve(),
    ), patch(
        "backend.App.orchestration.application.tasks.apply_auto_mcp_to_agent_config",
        return_value={},
    ), patch(
        "backend.App.orchestration.application.tasks.collect_workspace_file_index",
        return_value=("index text", 7),
    ), patch(
        "backend.App.orchestration.application.tasks.build_input_with_workspace",
        return_value="assembled",
    ):
        effective, path, meta = prepare_workspace(
            "task", str(tmp_path), False, agent_config={}
        )

    assert meta["workspace_context_mcp_fallback"] is True
    assert meta["workspace_section_title"] == "Workspace index"


def test_prepare_workspace_priority_paths_mode(tmp_path):
    """PRIORITY_PATHS mode calls collect_workspace_priority_snapshot."""
    with patch(
        "backend.App.orchestration.application.tasks.resolve_workspace_context_mode",
        return_value="priority_paths",
    ), patch(
        "backend.App.orchestration.application.tasks.workspace_write_allowed",
        return_value=True,
    ), patch(
        "backend.App.orchestration.application.tasks.validate_workspace_root",
        return_value=tmp_path.resolve(),
    ), patch(
        "backend.App.orchestration.application.tasks.collect_workspace_priority_snapshot",
        return_value=("priority snapshot", 3),
    ), patch(
        "backend.App.orchestration.application.tasks.build_input_with_workspace",
        return_value="assembled",
    ):
        effective, path, meta = prepare_workspace(
            "task", str(tmp_path), False
        )

    assert meta["workspace_snapshot"] == "priority snapshot"
    assert meta["workspace_snapshot_files"] == 3


def test_prepare_workspace_post_analysis_compact_mode(tmp_path):
    """POST_ANALYSIS_COMPACT mode calls collect_workspace_snapshot."""
    with patch(
        "backend.App.orchestration.application.tasks.resolve_workspace_context_mode",
        return_value="post_analysis_compact",
    ), patch(
        "backend.App.orchestration.application.tasks.workspace_write_allowed",
        return_value=True,
    ), patch(
        "backend.App.orchestration.application.tasks.validate_workspace_root",
        return_value=tmp_path.resolve(),
    ), patch(
        "backend.App.orchestration.application.tasks.collect_workspace_snapshot",
        return_value=("compact snapshot", 12),
    ), patch(
        "backend.App.orchestration.application.tasks.build_input_with_workspace",
        return_value="assembled",
    ):
        effective, path, meta = prepare_workspace(
            "task", str(tmp_path), False
        )

    assert meta["workspace_snapshot"] == "compact snapshot"


def test_start_pipeline_run_human_approval_oserror_writing_pipeline_json(tmp_path):
    """OSError when writing pipeline.json in human-approval path is logged, not raised."""
    mock_task_store = MagicMock()
    exc = HumanApprovalRequired("human_spec", "needs approval")
    exc.partial_state = {}
    exc.resume_pipeline_step = "human_spec"

    def bad_snapshot(s):
        raise OSError("disk full")

    with patch(
        "backend.App.integrations.infrastructure.observability.logging_config.set_task_id",
    ), patch(
        "backend.App.orchestration.application.tasks.run_pipeline",
        side_effect=exc,
    ):
        result = start_pipeline_run(
            user_prompt="do X",
            effective_prompt="full prompt",
            agent_config={},
            steps=["pm"],
            workspace_root_str="",
            workspace_apply_writes=False,
            workspace_path=None,
            workspace_meta={},
            task_id="task-human-oserror",
            task_store=mock_task_store,
            artifacts_root=tmp_path,
            pipeline_snapshot_for_disk=bad_snapshot,
            workspace_followup_lines=lambda *a: [],
        )

    assert result["status"] == "awaiting_human"


def test_start_pipeline_run_success_oserror_writing_pipeline_json(tmp_path):
    """OSError when writing pipeline.json in success path is logged, not raised."""
    mock_task_store = MagicMock()

    def bad_snapshot(s):
        raise OSError("disk full")

    with patch(
        "backend.App.integrations.infrastructure.observability.logging_config.set_task_id",
    ), patch(
        "backend.App.orchestration.application.tasks.run_pipeline",
        return_value={"pm_output": "done"},
    ), patch(
        "backend.App.orchestration.application.tasks.final_pipeline_user_message",
        return_value="final",
    ), patch(
        "backend.App.orchestration.application.tasks.task_store_agent_label",
        return_value="pm",
    ), patch(
        "backend.App.orchestration.application.tasks.workspace_write_allowed",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.tasks.ARTIFACT_AGENT_OUTPUT_KEYS",
        [],
    ), patch(
        "backend.App.orchestration.application.tasks.append_task_run_log",
    ):
        result = start_pipeline_run(
            user_prompt="do X",
            effective_prompt="full prompt",
            agent_config={},
            steps=["pm"],
            workspace_root_str="",
            workspace_apply_writes=False,
            workspace_path=None,
            workspace_meta={},
            task_id="task-oserror",
            task_store=mock_task_store,
            artifacts_root=tmp_path,
            pipeline_snapshot_for_disk=bad_snapshot,
            workspace_followup_lines=lambda *a: [],
        )

    assert result["status"] == "completed"


def test_start_pipeline_run_success_crole_artifacts(tmp_path):
    """Custom role outputs (crole_*_output keys) are written as artifacts."""
    mock_task_store = MagicMock()
    mock_result = {
        "crole_myagent_output": "custom agent result",
    }

    with patch(
        "backend.App.integrations.infrastructure.observability.logging_config.set_task_id",
    ), patch(
        "backend.App.orchestration.application.tasks.run_pipeline",
        return_value=mock_result,
    ), patch(
        "backend.App.orchestration.application.tasks.final_pipeline_user_message",
        return_value="final",
    ), patch(
        "backend.App.orchestration.application.tasks.task_store_agent_label",
        return_value="crole_myagent",
    ), patch(
        "backend.App.orchestration.application.tasks.workspace_write_allowed",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.tasks.ARTIFACT_AGENT_OUTPUT_KEYS",
        [],
    ), patch(
        "backend.App.orchestration.application.tasks.append_task_run_log",
    ):
        result = start_pipeline_run(
            user_prompt="do X",
            effective_prompt="full prompt",
            agent_config={},
            steps=["crole_myagent"],
            workspace_root_str="",
            workspace_apply_writes=False,
            workspace_path=None,
            workspace_meta={},
            task_id="task-crole",
            task_store=mock_task_store,
            artifacts_root=tmp_path,
            pipeline_snapshot_for_disk=lambda s: s,
            workspace_followup_lines=lambda *a: [],
        )

    assert result["status"] == "completed"
    artifact_file = tmp_path / "task-crole" / "agents" / "crole_myagent.txt"
    assert artifact_file.exists()
    assert artifact_file.read_text() == "custom agent result"


def test_start_pipeline_run_workspace_writes_applied(tmp_path):
    """When workspace_path and workspace_apply_writes=True, writes are applied."""
    mock_task_store = MagicMock()
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    with patch(
        "backend.App.integrations.infrastructure.observability.logging_config.set_task_id",
    ), patch(
        "backend.App.orchestration.application.tasks.run_pipeline",
        return_value={"dev_output": "code"},
    ), patch(
        "backend.App.orchestration.application.tasks.final_pipeline_user_message",
        return_value="final",
    ), patch(
        "backend.App.orchestration.application.tasks.task_store_agent_label",
        return_value="dev",
    ), patch(
        "backend.App.orchestration.application.tasks.workspace_write_allowed",
        return_value=True,
    ), patch(
        "backend.App.orchestration.application.tasks.ARTIFACT_AGENT_OUTPUT_KEYS",
        [],
    ), patch(
        "backend.App.orchestration.application.tasks.run_shell_after_user_approval",
        return_value=MagicMock(),
    ), patch(
        "backend.App.orchestration.application.tasks.apply_from_devops_and_dev_outputs",
        return_value={"written": ["file.py"], "patched": [], "udiff_applied": [], "parsed": 1, "errors": []},
    ), patch(
        "backend.App.orchestration.application.tasks.append_task_run_log",
    ):
        result = start_pipeline_run(
            user_prompt="do X",
            effective_prompt="full prompt",
            agent_config={},
            steps=["dev"],
            workspace_root_str=str(workspace_dir),
            workspace_apply_writes=True,
            workspace_path=workspace_dir,
            workspace_meta={},
            task_id="task-writes",
            task_store=mock_task_store,
            artifacts_root=tmp_path,
            pipeline_snapshot_for_disk=lambda s: s,
            workspace_followup_lines=lambda *a: [],
        )

    assert result["status"] == "completed"

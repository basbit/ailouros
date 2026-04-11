"""write_pipeline_step_to_workspace: writing planning outputs to .swarm/spec/."""
from pathlib import Path

from backend.App.workspace.application.doc_workspace import write_pipeline_step_to_workspace


def _state(tmp_path: Path, *, apply_writes: bool = True) -> dict:
    return {
        "workspace_root": str(tmp_path),
        "workspace_apply_writes": apply_writes,
        "task_id": "test-task-1",
        "agent_config": {},
    }


def test_writes_pm_output(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    result = write_pipeline_step_to_workspace(_state(tmp_path), "pm", "# PM output\n\nContent")
    assert result == ".swarm/spec/pm_output.md"
    assert (tmp_path / ".swarm" / "spec" / "pm_output.md").read_text() == "# PM output\n\nContent"


def test_writes_ba_output(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    result = write_pipeline_step_to_workspace(_state(tmp_path), "ba", "ba content")
    assert result == ".swarm/spec/ba_output.md"


def test_writes_arch_output(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    result = write_pipeline_step_to_workspace(_state(tmp_path), "architect", "arch content")
    assert result == ".swarm/spec/arch_output.md"


def test_writes_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    result = write_pipeline_step_to_workspace(_state(tmp_path), "spec", "spec content")
    assert result == ".swarm/spec/spec.md"


def test_unknown_step_uses_step_name_as_filename(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    result = write_pipeline_step_to_workspace(_state(tmp_path), "custom_step", "content")
    assert result == ".swarm/spec/custom_step.md"


def test_no_write_without_workspace_apply_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    result = write_pipeline_step_to_workspace(_state(tmp_path, apply_writes=False), "pm", "content")
    assert result is None
    assert not (tmp_path / ".swarm").exists()


def test_no_write_without_env_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARM_ALLOW_WORKSPACE_WRITE", raising=False)
    result = write_pipeline_step_to_workspace(_state(tmp_path), "pm", "content")
    assert result is None


def test_no_write_when_disabled_in_swarm_config(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    state = _state(tmp_path)
    state["agent_config"] = {"swarm": {"write_pipeline_steps_to_workspace": False}}
    result = write_pipeline_step_to_workspace(state, "pm", "content")
    assert result is None


def test_no_write_for_empty_content(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    result = write_pipeline_step_to_workspace(_state(tmp_path), "pm", "   ")
    assert result is None


def test_no_write_for_empty_workspace_root(monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    state = {"workspace_root": "", "workspace_apply_writes": True, "agent_config": {}}
    result = write_pipeline_step_to_workspace(state, "pm", "content")
    assert result is None


def test_creates_parent_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    write_pipeline_step_to_workspace(_state(tmp_path), "ba", "ba output")
    assert (tmp_path / ".swarm" / "spec").is_dir()


def test_overwrites_existing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    state = _state(tmp_path)
    write_pipeline_step_to_workspace(state, "pm", "first version")
    write_pipeline_step_to_workspace(state, "pm", "second version")
    assert (tmp_path / ".swarm" / "spec" / "pm_output.md").read_text() == "second version"

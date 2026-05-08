import json

from backend.App.orchestration.infrastructure.pipeline_artifact_reader import (
    infer_failed_step_from_flat_snapshot,
    load_partial_pipeline_state,
    reconstruct_partial_state_from_flat_snapshot,
)


def test_reconstruct_filters_meta_keys():
    flat_snapshot = {
        "pm_output": "planning",
        "dev_output": "code",
        "error": "something failed",
        "failed_step": "dev",
        "partial_state": {"unused": "data"},
        "resume_from_step": "dev",
    }
    reconstructed = reconstruct_partial_state_from_flat_snapshot(flat_snapshot)
    assert reconstructed["pm_output"] == "planning"
    assert reconstructed["dev_output"] == "code"
    assert "error" not in reconstructed
    assert "failed_step" not in reconstructed
    assert "partial_state" not in reconstructed
    assert "resume_from_step" not in reconstructed


def test_reconstruct_maps_disk_key_to_state_key_for_architect():
    flat_snapshot = {
        "pm_output": "pm",
        "architect_output": "architect produced this",
        "architect_model": "gpt-4",
        "architect_provider": "openai",
    }
    reconstructed = reconstruct_partial_state_from_flat_snapshot(flat_snapshot)
    assert reconstructed["architect_output"] == "architect produced this"
    assert reconstructed["arch_output"] == "architect produced this"
    assert reconstructed["arch_model"] == "gpt-4"
    assert reconstructed["arch_provider"] == "openai"


def test_reconstruct_does_not_override_existing_state_key():
    flat_snapshot = {
        "arch_output": "original state value",
        "architect_output": "disk event value",
    }
    reconstructed = reconstruct_partial_state_from_flat_snapshot(flat_snapshot)
    assert reconstructed["arch_output"] == "original state value"


def test_infer_failed_step_returns_first_missing_output():
    pipeline_steps = ["clarify_input", "pm", "architect", "dev"]
    artifact_output_key_by_step = {
        "clarify_input": "clarify_input_output",
        "pm": "pm_output",
        "architect": "arch_output",
        "dev": "dev_output",
    }
    flat_snapshot = {
        "clarify_input_output": "ok",
        "pm_output": "ok",
        "arch_output": "",
    }
    result = infer_failed_step_from_flat_snapshot(
        flat_snapshot, pipeline_steps, artifact_output_key_by_step,
    )
    assert result == "architect"


def test_infer_failed_step_returns_empty_when_all_present():
    pipeline_steps = ["pm", "dev"]
    artifact_output_key_by_step = {"pm": "pm_output", "dev": "dev_output"}
    flat_snapshot = {"pm_output": "a", "dev_output": "b"}
    assert infer_failed_step_from_flat_snapshot(
        flat_snapshot, pipeline_steps, artifact_output_key_by_step,
    ) == ""


def test_load_partial_pipeline_state_prefers_explicit_partial(tmp_path):
    task_id = "task-prefer-explicit"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    (task_dir / "pipeline.json").write_text(json.dumps({
        "pm_output": "flat",
        "partial_state": {"pm_output": "explicit"},
    }))
    result = load_partial_pipeline_state(task_id, artifacts_dir=tmp_path)
    assert result["pm_output"] == "explicit"


def test_load_partial_pipeline_state_falls_back_to_flat(tmp_path):
    task_id = "task-fallback-flat"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    (task_dir / "pipeline.json").write_text(json.dumps({
        "pm_output": "planning done",
        "dev_output": "",
        "error": "crash",
    }))
    result = load_partial_pipeline_state(task_id, artifacts_dir=tmp_path)
    assert result["pm_output"] == "planning done"
    assert "error" not in result


def test_load_partial_pipeline_state_returns_empty_for_missing_file(tmp_path):
    assert load_partial_pipeline_state("nonexistent-task", artifacts_dir=tmp_path) == {}

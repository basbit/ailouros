"""Tests for K-3: Intra-Pipeline Checkpoints."""
from __future__ import annotations

from backend.App.orchestration.application.sessions.checkpoint_manager import CheckpointManager


def test_save_and_get_latest():
    mgr = CheckpointManager()
    mgr.save("task1", "step_pm", 0, {"pm_output": "hello"})
    cp = mgr.get_latest("task1")
    assert cp is not None
    assert cp.step_id == "step_pm"
    assert cp.state_snapshot == {"pm_output": "hello"}


def test_get_latest_returns_last():
    mgr = CheckpointManager()
    mgr.save("task2", "step_pm", 0, {"output": "a"})
    mgr.save("task2", "step_dev", 1, {"output": "b"})
    cp = mgr.get_latest("task2")
    assert cp is not None
    assert cp.step_id == "step_dev"
    assert cp.state_snapshot == {"output": "b"}


def test_get_latest_missing_task_returns_none():
    mgr = CheckpointManager()
    assert mgr.get_latest("nonexistent") is None


def test_get_by_step():
    mgr = CheckpointManager()
    mgr.save("task3", "step_pm", 0, {"pm_output": "x"})
    mgr.save("task3", "step_dev", 1, {"dev_output": "y"})
    cp = mgr.get_by_step("task3", "step_pm")
    assert cp is not None
    assert cp.step_id == "step_pm"
    assert cp.state_snapshot == {"pm_output": "x"}


def test_get_by_step_missing_returns_none():
    mgr = CheckpointManager()
    mgr.save("task4", "step_pm", 0, {})
    assert mgr.get_by_step("task4", "step_nonexistent") is None


def test_list_checkpoints():
    mgr = CheckpointManager()
    mgr.save("task5", "step_pm", 0, {"a": 1})
    mgr.save("task5", "step_dev", 1, {"b": 2})
    mgr.save("task5", "step_qa", 2, {"c": 3})
    listed = mgr.list_checkpoints("task5")
    assert len(listed) == 3
    assert listed[0]["step_id"] == "step_pm"
    assert listed[2]["step_id"] == "step_qa"


def test_list_checkpoints_empty():
    mgr = CheckpointManager()
    assert mgr.list_checkpoints("no_such_task") == []


def test_list_checkpoints_schema_version():
    mgr = CheckpointManager()
    mgr.save("task6", "step_pm", 0, {})
    listed = mgr.list_checkpoints("task6")
    assert listed[0]["schema_version"] == "1"
    assert "timestamp_utc" in listed[0]


def test_resume_state():
    mgr = CheckpointManager()
    state = {"pm_output": "done", "dev_output": "also done"}
    mgr.save("task7", "step_dev", 1, state)
    resumed = mgr.resume_state("task7", "step_dev")
    assert resumed == state


def test_resume_from_checkpoint():
    """Simulate resuming a pipeline from step 2 — verify state from checkpoint is returned."""
    mgr = CheckpointManager()
    mgr.save("taskA", "step_pm", 0, {"pm_output": "pm_done"})
    mgr.save("taskA", "step_ba", 1, {"pm_output": "pm_done", "ba_output": "ba_done"})
    mgr.save("taskA", "step_arch", 2, {"pm_output": "pm_done", "ba_output": "ba_done", "arch_output": "arch_done"})

    # Resume from step_ba
    resumed = mgr.resume_state("taskA", "step_ba")
    assert resumed is not None
    assert resumed["ba_output"] == "ba_done"
    # Verify step_arch checkpoint exists and has both
    cp_arch = mgr.get_by_step("taskA", "step_arch")
    assert cp_arch is not None
    assert cp_arch.step_index == 2


def test_resume_state_missing_step_returns_none():
    mgr = CheckpointManager()
    mgr.save("taskB", "step_pm", 0, {})
    assert mgr.resume_state("taskB", "step_nonexistent") is None


def test_clear():
    mgr = CheckpointManager()
    mgr.save("taskC", "step_pm", 0, {})
    mgr.save("taskC", "step_dev", 1, {})
    assert len(mgr.list_checkpoints("taskC")) == 2
    mgr.clear("taskC")
    assert mgr.list_checkpoints("taskC") == []
    assert mgr.get_latest("taskC") is None


def test_clear_nonexistent_is_noop():
    mgr = CheckpointManager()
    mgr.clear("does_not_exist")  # Should not raise


def test_every_n_steps_skips():
    mgr = CheckpointManager(every_n_steps=2)
    # step_index 0: skipped (0+1=1, 1%2 != 0 but step_index==0 so saved)
    mgr.save("taskD", "step_pm", 0, {"i": 0})
    # step_index 1: 1%2 != 0, but (1+1)%2==0 so saved
    mgr.save("taskD", "step_ba", 1, {"i": 1})
    # step_index 2: (2+1)%2 != 0, so skipped
    mgr.save("taskD", "step_arch", 2, {"i": 2})
    # step_index 3: (3+1)%2==0, so saved
    mgr.save("taskD", "step_dev", 3, {"i": 3})
    listed = mgr.list_checkpoints("taskD")
    step_ids = [c["step_id"] for c in listed]
    # step 0 always saved, step 1 saved (index+1=2, 2%2==0), step 2 skipped, step 3 saved
    assert "step_pm" in step_ids
    assert "step_ba" in step_ids
    assert "step_arch" not in step_ids
    assert "step_dev" in step_ids


def test_state_snapshot_is_copy():
    """Mutating source state after save must not affect stored checkpoint."""
    mgr = CheckpointManager()
    state = {"x": 1}
    mgr.save("taskE", "step_pm", 0, state)
    state["x"] = 99
    cp = mgr.get_latest("taskE")
    assert cp is not None
    assert cp.state_snapshot["x"] == 1

from __future__ import annotations

from backend.App.shared.infrastructure import activity_recorder
from backend.App.shared.infrastructure.task_context import bind_active_task


def test_bind_active_task_sets_both_contexts(monkeypatch):
    calls: list[str] = []

    def fake_set_task_id(task_id: str) -> None:
        calls.append(("logging", task_id))

    monkeypatch.setattr(
        "backend.App.integrations.infrastructure.observability.logging_config.set_task_id",
        fake_set_task_id,
    )
    bind_active_task("task-7")
    assert ("logging", "task-7") in calls
    assert activity_recorder.active_task() == "task-7"


def test_bind_active_task_with_none_clears(monkeypatch):
    activity_recorder.set_active_task("stale")
    monkeypatch.setattr(
        "backend.App.integrations.infrastructure.observability.logging_config.set_task_id",
        lambda _value: None,
    )
    bind_active_task(None)
    assert activity_recorder.active_task() is None


def test_bind_active_task_strips_whitespace(monkeypatch):
    received: list[str] = []
    monkeypatch.setattr(
        "backend.App.integrations.infrastructure.observability.logging_config.set_task_id",
        lambda value: received.append(value),
    )
    bind_active_task("  task-8  ")
    assert received[-1] == "task-8"
    assert activity_recorder.active_task() == "task-8"

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from backend.App.orchestration.application.streaming.pipeline_sse_handler import (
    PipelineSSEHandler,
)


class _FakeTaskStore:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def update_task(
        self,
        task_id: str,
        *,
        status: Optional[str] = None,
        agent: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None:
        self.updates.append(
            {"task_id": task_id, "status": status, "agent": agent, "message": message}
        )

    def get_task(self, *_a: Any, **_kw: Any) -> dict[str, Any]:
        return {}


_PAUSED_BA_OUTPUT = (
    "Initial analysis:\n\n"
    "NEEDS_CLARIFICATION\n"
    "Questions for the user:\n"
    "1. Which database should we use?\n"
    "2. Should authentication be required?\n"
)


def _run_handler(
    events: list[dict[str, Any]],
    tmp_path: Path,
    *,
    pipeline_snapshot: Optional[dict[str, Any]] = None,
) -> tuple[list[str], _FakeTaskStore, dict[str, Any], Path]:
    task_dir = tmp_path / "task-clarif"
    agents_dir = task_dir / "agents"
    task_dir.mkdir(parents=True, exist_ok=True)
    agents_dir.mkdir(parents=True, exist_ok=True)

    written: list[tuple[str, str]] = []

    def _artifact_writer(_dir: Path, agent: str, text: str) -> None:
        written.append((agent, text))

    store = _FakeTaskStore()
    handler = PipelineSSEHandler(task_store=store, artifact_writer=_artifact_writer)
    snapshot: dict[str, Any] = pipeline_snapshot or {
        "agent_config": {"reviewer": {"model": "x"}},
        "pipeline_steps": ["pm", "ba", "architect", "qa"],
        "input": "build something",
    }
    chunks = list(
        handler.handle_events(
            events_gen=iter(events),
            task_id="t-clarif",
            task_dir=task_dir,
            agents_dir=agents_dir,
            pipeline_snapshot=snapshot,
            now=1700000000,
            request_model="test-model",
            workspace_path=None,
            workspace_apply_writes=False,
            cancel_event=None,
        )
    )
    return chunks, store, snapshot, task_dir


def test_pipeline_pauses_on_needs_clarification_from_ba(tmp_path: Path):
    events = [
        {"agent": "pm", "status": "in_progress", "message": "pm start"},
        {"agent": "pm", "status": "completed", "message": "pm output ready"},
        {"agent": "ba", "status": "in_progress", "message": "ba thinking"},
        {"agent": "ba", "status": "completed", "message": _PAUSED_BA_OUTPUT},
        {"agent": "architect", "status": "in_progress", "message": "should never reach"},
    ]
    chunks, store, snapshot, task_dir = _run_handler(events, tmp_path)

    assert any('"awaiting_clarification"' in chunk for chunk in chunks)
    assert any('"step_id": "ba"' in chunk for chunk in chunks)
    assert chunks[-1] == "data: [DONE]\n\n"

    assert snapshot["clarification_pause"]["step_id"] == "ba"
    assert snapshot["resume_from_step"] == "ba"
    assert snapshot["human_approval_step"] == "ba"
    assert snapshot["ba_output"] == _PAUSED_BA_OUTPUT

    awaiting_updates = [u for u in store.updates if u["status"] == "awaiting_human"]
    assert awaiting_updates and awaiting_updates[-1]["agent"] == "ba"

    arch_messages = [u for u in store.updates if u["agent"] == "architect"]
    assert arch_messages == []

    persisted = json.loads((task_dir / "pipeline.json").read_text(encoding="utf-8"))
    assert persisted["clarification_pause"]["step_id"] == "ba"
    assert persisted["resume_from_step"] == "ba"


def test_pipeline_does_not_pause_without_marker(tmp_path: Path):
    events = [
        {"agent": "pm", "status": "in_progress", "message": "pm start"},
        {"agent": "pm", "status": "completed", "message": "pm output"},
        {"agent": "ba", "status": "completed", "message": "ba output (no marker)"},
    ]
    chunks, _store, snapshot, _task_dir = _run_handler(events, tmp_path)
    assert not any('"awaiting_clarification"' in chunk for chunk in chunks)
    assert "clarification_pause" not in snapshot


def test_pipeline_does_not_pause_for_media_role_by_default(tmp_path: Path):
    events = [
        {
            "agent": "image_generator",
            "status": "completed",
            "message": _PAUSED_BA_OUTPUT,
        },
    ]
    chunks, _store, snapshot, _task_dir = _run_handler(events, tmp_path)
    assert not any('"awaiting_clarification"' in chunk for chunk in chunks)
    assert "clarification_pause" not in snapshot


def test_resume_threads_answers_into_paused_step(tmp_path: Path, monkeypatch):
    from backend.App.orchestration.application.streaming import resume_stream as _rs

    pipeline_snapshot: dict[str, Any] = {
        "agent_config": {"reviewer": {"model": "x"}},
        "pipeline_steps": ["pm", "ba", "architect"],
        "input": "build something",
        "pm_output": "pm output",
        "ba_output": _PAUSED_BA_OUTPUT,
        "clarification_pause": {
            "step_id": "ba",
            "reason": "needs_clarification",
            "questions": [
                {"index": 1, "text": "Which database?", "options": []},
                {"index": 2, "text": "Auth required?", "options": []},
            ],
        },
        "resume_from_step": "ba",
        "human_approval_step": "ba",
        "partial_state": {
            "agent_config": {"reviewer": {"model": "x"}},
            "pipeline_steps": ["pm", "ba", "architect"],
            "input": "build something",
            "pm_output": "pm output",
            "ba_output": _PAUSED_BA_OUTPUT,
        },
    }

    artifacts_root = tmp_path / "artifacts"
    task_dir = artifacts_root / "t-clarif"
    agents_dir = task_dir / "agents"
    task_dir.mkdir(parents=True, exist_ok=True)
    agents_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "pipeline.json").write_text(
        json.dumps(pipeline_snapshot, ensure_ascii=False), encoding="utf-8"
    )

    captured_calls: dict[str, Any] = {}

    def _fake_run_pipeline_stream_resume_clarification(
        partial_state, pipeline_steps, paused_step, human_answers, cancel_event=None
    ):
        captured_calls["partial_state"] = partial_state
        captured_calls["pipeline_steps"] = list(pipeline_steps)
        captured_calls["paused_step"] = paused_step
        captured_calls["human_answers"] = human_answers
        new_state = dict(partial_state)
        answers_map = dict(new_state.get("clarification_answers") or {})
        answers_map[paused_step] = human_answers
        new_state["clarification_answers"] = answers_map
        new_state["ba_output"] = (
            "Refined ba output using user answers: " + human_answers
        )
        new_state["architect_output"] = "architect done"
        yield {"agent": "ba", "status": "in_progress", "message": "ba retry"}
        yield {
            "agent": "ba",
            "status": "completed",
            "message": new_state["ba_output"],
        }
        yield {
            "agent": "architect",
            "status": "completed",
            "message": "architect done",
        }
        return new_state

    monkeypatch.setattr(
        _rs,
        "run_pipeline_stream_resume_clarification",
        _fake_run_pipeline_stream_resume_clarification,
    )
    monkeypatch.setattr(
        _rs, "apply_final_workspace_writes", lambda *a, **k: None
    )
    monkeypatch.setattr(
        _rs, "workspace_followup_lines", lambda *a, **k: []
    )

    class _FakeTaskStoreLocal:
        def __init__(self) -> None:
            self.updates: list[dict[str, Any]] = []

        def update_task(
            self,
            task_id: str,
            *,
            status: Optional[str] = None,
            agent: Optional[str] = None,
            message: Optional[str] = None,
        ) -> None:
            self.updates.append(
                {"task_id": task_id, "status": status, "agent": agent, "message": message}
            )

    store = _FakeTaskStoreLocal()

    chunks = list(
        _rs.stream_human_resume_chunks(
            task_id="t-clarif",
            human_feedback="A: postgres; B: yes",
            request_model="test-model",
            artifacts_root=artifacts_root,
            task_store=store,
        )
    )

    assert captured_calls["paused_step"] == "ba"
    assert captured_calls["pipeline_steps"] == ["pm", "ba", "architect"]
    assert "postgres" in captured_calls["human_answers"]
    assert "yes" in captured_calls["human_answers"]

    final_snapshot = json.loads((task_dir / "pipeline.json").read_text(encoding="utf-8"))
    assert "clarification_pause" not in final_snapshot
    assert "resume_from_step" not in final_snapshot
    assert "human_approval_step" not in final_snapshot

    assert any(
        u["status"] == "completed" for u in store.updates
    ), "task should reach completed status after clarification resume"

    assert chunks and chunks[-1] == "data: [DONE]\n\n"

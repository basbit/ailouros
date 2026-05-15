from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from backend.App.orchestration.application.streaming.clarification_pause import (
    PauseDecision,
    emit_pause_events,
    evaluate_step_clarification,
    handle_step_clarification,
)


class _StubTaskStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def update_task(
        self,
        task_id: str,
        *,
        status: Optional[str] = None,
        agent: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None:
        self.calls.append(
            {"task_id": task_id, "status": status, "agent": agent, "message": message}
        )


_PAUSED_OUTPUT = (
    "Some analysis...\n\n"
    "NEEDS_CLARIFICATION\n"
    "Questions for the user:\n"
    "1. Which database should be used?\n"
    "2. Should auth be optional?\n"
)


def _build_snapshot(role: str = "ba", role_cfg: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    agent_config: dict[str, Any] = {"reviewer": {"model": "x"}}
    if role_cfg is not None:
        agent_config[role] = role_cfg
    return {
        "agent_config": agent_config,
        "pipeline_steps": ["clarify_input", "pm", role, "qa"],
        "input": "build a thing",
    }


def test_evaluate_returns_decision_for_needs_clarification():
    snapshot = _build_snapshot("ba")
    decision = evaluate_step_clarification("ba", _PAUSED_OUTPUT, snapshot)
    assert isinstance(decision, PauseDecision)
    assert decision.step_id == "ba"
    assert len(decision.questions) == 2
    assert "database" in decision.questions[0].text.lower()
    assert decision.resume_payload["reason"] == "needs_clarification"


def test_evaluate_returns_none_when_no_marker():
    snapshot = _build_snapshot("ba")
    assert evaluate_step_clarification("ba", "plain ba output", snapshot) is None


def test_evaluate_respects_media_role_default():
    snapshot = _build_snapshot("image_generator")
    assert evaluate_step_clarification("image_generator", _PAUSED_OUTPUT, snapshot) is None


def test_evaluate_explicit_role_flag_overrides_media_default():
    snapshot = _build_snapshot(
        "image_generator", role_cfg={"can_request_clarification": True}
    )
    decision = evaluate_step_clarification("image_generator", _PAUSED_OUTPUT, snapshot)
    assert decision is not None
    assert decision.step_id == "image_generator"


def test_evaluate_explicit_disable_for_text_role():
    snapshot = _build_snapshot("ba", role_cfg={"can_request_clarification": False})
    assert evaluate_step_clarification("ba", _PAUSED_OUTPUT, snapshot) is None


def test_handle_step_clarification_persists_pause_state(tmp_path: Path):
    snapshot: dict[str, Any] = _build_snapshot("ba")
    snapshot["ba_output"] = _PAUSED_OUTPUT
    task_store = _StubTaskStore()
    task_dir = tmp_path / "task1"
    agents_dir = task_dir / "agents"

    decision = handle_step_clarification(
        step_id="ba",
        output=_PAUSED_OUTPUT,
        pipeline_snapshot=snapshot,
        task_store=task_store,
        task_id="task1",
        task_dir=task_dir,
        agents_dir=agents_dir,
        now=12345,
        request_model="test-model",
    )

    assert decision is not None
    assert decision.step_id == "ba"
    assert snapshot["clarification_pause"]["step_id"] == "ba"
    assert snapshot["resume_from_step"] == "ba"
    assert snapshot["human_approval_step"] == "ba"
    assert isinstance(snapshot["partial_state"], dict)
    assert snapshot["partial_state"]["ba_output"] == _PAUSED_OUTPUT
    assert "clarification_pause" not in snapshot["partial_state"]
    assert task_store.calls and task_store.calls[-1]["status"] == "awaiting_human"
    assert task_store.calls[-1]["agent"] == "ba"
    persisted = json.loads((task_dir / "pipeline.json").read_text(encoding="utf-8"))
    assert persisted["clarification_pause"]["step_id"] == "ba"


def test_handle_step_clarification_returns_none_when_no_marker(tmp_path: Path):
    snapshot = _build_snapshot("ba")
    task_store = _StubTaskStore()
    task_dir = tmp_path / "taskN"
    agents_dir = task_dir / "agents"

    result = handle_step_clarification(
        step_id="ba",
        output="boring output without marker",
        pipeline_snapshot=snapshot,
        task_store=task_store,
        task_id="taskN",
        task_dir=task_dir,
        agents_dir=agents_dir,
        now=1,
        request_model="m",
    )
    assert result is None
    assert "clarification_pause" not in snapshot
    assert task_store.calls == []
    assert not (task_dir / "pipeline.json").exists()


def test_handle_step_clarification_respects_role_disable(tmp_path: Path):
    snapshot = _build_snapshot("ba", role_cfg={"can_request_clarification": False})
    task_store = _StubTaskStore()
    task_dir = tmp_path / "taskD"
    agents_dir = task_dir / "agents"

    result = handle_step_clarification(
        step_id="ba",
        output=_PAUSED_OUTPUT,
        pipeline_snapshot=snapshot,
        task_store=task_store,
        task_id="taskD",
        task_dir=task_dir,
        agents_dir=agents_dir,
        now=1,
        request_model="m",
    )
    assert result is None
    assert "clarification_pause" not in snapshot


def test_emit_pause_events_yields_awaiting_clarification():
    snapshot = _build_snapshot("architect")
    decision = evaluate_step_clarification("architect", _PAUSED_OUTPUT, snapshot)
    assert decision is not None
    chunks = list(emit_pause_events(decision, now=999, request_model="m"))
    assert any('"awaiting_clarification"' in chunk for chunk in chunks)
    assert any('"step_id": "architect"' in chunk for chunk in chunks)
    assert chunks[-1] == "data: [DONE]\n\n"


@pytest.mark.parametrize("role", ["ba", "architect", "dev_lead", "qa", "review_pm"])
def test_evaluate_supports_planning_roles(role: str):
    snapshot = _build_snapshot(role)
    decision = evaluate_step_clarification(role, _PAUSED_OUTPUT, snapshot)
    assert decision is not None
    assert decision.step_id == role

"""Regression: SSE stream must emit a heartbeat when a step stalls.

Bug: task aec02899 stopped emitting SSE events while the reviewer LLM
call was stuck. The UI saw nothing for 3 hours. Heartbeat events should
mark the stream as "still alive" even when the step function has no
own progress to report.
"""
from __future__ import annotations

import time

import pytest

from backend.App.orchestration.infrastructure.step_stream_executor import (
    StepStreamExecutor,
    _step_heartbeat_interval_sec,
)


def test_heartbeat_default_interval(monkeypatch):
    monkeypatch.delenv("SWARM_STEP_HEARTBEAT_SEC", raising=False)
    assert _step_heartbeat_interval_sec() == 15.0


def test_heartbeat_env_override(monkeypatch):
    monkeypatch.setenv("SWARM_STEP_HEARTBEAT_SEC", "3.5")
    assert _step_heartbeat_interval_sec() == pytest.approx(3.5)


def test_heartbeat_disabled_when_zero(monkeypatch):
    monkeypatch.setenv("SWARM_STEP_HEARTBEAT_SEC", "0")
    assert _step_heartbeat_interval_sec() == 0.0


def test_heartbeat_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("SWARM_STEP_HEARTBEAT_SEC", "banana")
    assert _step_heartbeat_interval_sec() == 15.0


def test_step_emits_heartbeat_when_silent(monkeypatch):
    """A step function that does nothing for > heartbeat interval must
    still produce at least one heartbeat event so SSE doesn't go dark."""
    monkeypatch.setenv("SWARM_STEP_HEARTBEAT_SEC", "0.2")

    def _slow_step(state):
        time.sleep(0.7)  # 3+ heartbeat intervals
        return {"pm_output": "done"}

    executor = StepStreamExecutor()
    events = list(executor.run("pm", _slow_step, {}))

    heartbeats = [e for e in events if e.get("status") == "heartbeat"]
    assert heartbeats, (
        f"expected heartbeat events from a 0.7s-silent step; got: {events}"
    )
    assert all(e.get("agent") == "pm" for e in heartbeats)
    assert all("elapsed_sec" in e for e in heartbeats)


def test_step_no_heartbeat_when_disabled(monkeypatch):
    monkeypatch.setenv("SWARM_STEP_HEARTBEAT_SEC", "0")

    def _slow_step(state):
        time.sleep(0.5)
        return {"pm_output": "done"}

    executor = StepStreamExecutor()
    events = list(executor.run("pm", _slow_step, {}))
    assert not any(e.get("status") == "heartbeat" for e in events)


def test_step_no_heartbeat_when_step_is_fast(monkeypatch):
    """A step that completes before the heartbeat interval shouldn't emit one."""
    monkeypatch.setenv("SWARM_STEP_HEARTBEAT_SEC", "5")

    def _fast_step(state):
        return {"pm_output": "done"}

    executor = StepStreamExecutor()
    events = list(executor.run("pm", _fast_step, {}))
    assert not any(e.get("status") == "heartbeat" for e in events)

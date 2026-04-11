"""Tests for backend/App/orchestration/infrastructure/agents/human_agent.py."""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from backend.App.orchestration.infrastructure.agents.human_agent import (
    HumanAgent,
    _human_gate_timeout_sec,
)
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired, HumanGateTimeout


# ---------------------------------------------------------------------------
# _human_gate_timeout_sec
# ---------------------------------------------------------------------------

def test_human_gate_timeout_default(monkeypatch):
    monkeypatch.delenv("SWARM_HUMAN_GATE_TIMEOUT_SEC", raising=False)
    assert _human_gate_timeout_sec() == 3600.0


def test_human_gate_timeout_custom(monkeypatch):
    monkeypatch.setenv("SWARM_HUMAN_GATE_TIMEOUT_SEC", "60.0")
    assert _human_gate_timeout_sec() == 60.0


def test_human_gate_timeout_invalid_uses_default(monkeypatch):
    monkeypatch.setenv("SWARM_HUMAN_GATE_TIMEOUT_SEC", "not-a-number")
    assert _human_gate_timeout_sec() == 3600.0


def test_human_gate_timeout_below_min(monkeypatch):
    monkeypatch.setenv("SWARM_HUMAN_GATE_TIMEOUT_SEC", "0.1")
    assert _human_gate_timeout_sec() == 1.0  # Clamps to 1.0


# ---------------------------------------------------------------------------
# HumanAgent — auto-approve via env
# ---------------------------------------------------------------------------

def test_human_agent_auto_approve_env(monkeypatch):
    monkeypatch.setenv("SWARM_HUMAN_AUTO_APPROVE", "1")
    agent = HumanAgent("human_pm")
    result = agent.run("Review output here")
    assert "APPROVED" in result
    assert "auto" in result.lower()


def test_human_agent_not_auto_approve_env_no_wait_event(monkeypatch):
    monkeypatch.setenv("SWARM_HUMAN_AUTO_APPROVE", "0")
    agent = HumanAgent("human_pm")
    with pytest.raises(HumanApprovalRequired) as exc_info:
        agent.run("Review output here")
    assert exc_info.value.step == "human_pm"


# ---------------------------------------------------------------------------
# HumanAgent — auto_approve from agent_config
# ---------------------------------------------------------------------------

def test_human_agent_auto_approve_config():
    agent = HumanAgent("human_ba", agent_config={"auto_approve": True})
    result = agent.run("Some context")
    assert "APPROVED" in result


def test_human_agent_no_auto_approve_config_no_wait_event():
    agent = HumanAgent("human_ba", agent_config={"auto_approve": False})
    with pytest.raises(HumanApprovalRequired) as exc_info:
        agent.run("Some context")
    assert "human_ba" in str(exc_info.value)


def test_human_agent_require_manual_overrides_auto():
    """require_manual=True forces interactive mode even with auto_approve=True."""
    agent = HumanAgent("human_dev", agent_config={"auto_approve": True, "require_manual": True})
    with pytest.raises(HumanApprovalRequired):
        agent.run("Some context")


# ---------------------------------------------------------------------------
# HumanAgent — context truncation
# ---------------------------------------------------------------------------

def test_human_agent_auto_approve_with_long_context():
    agent = HumanAgent("human_pm", agent_config={"auto_approve": True})
    long_context = "x" * 2000
    result = agent.run(long_context)
    assert "APPROVED" in result
    assert "2000" in result or "символов" in result


# ---------------------------------------------------------------------------
# HumanAgent — wait_event path
# ---------------------------------------------------------------------------

def test_human_agent_wait_event_approved():
    agent = HumanAgent("human_pm", agent_config={"auto_approve": False})
    event = threading.Event()

    def set_event_soon():
        time.sleep(0.05)
        event.set()

    t = threading.Thread(target=set_event_soon, daemon=True)
    t.start()
    result = agent.run("Context", wait_event=event)
    t.join(timeout=2)
    assert "APPROVED" in result


def test_human_agent_wait_event_cancelled():
    """Cancel event during wait raises HumanApprovalRequired."""
    agent = HumanAgent("human_pm", agent_config={"auto_approve": False})
    wait_ev = threading.Event()
    cancel_ev = threading.Event()

    def cancel_soon():
        time.sleep(0.05)
        cancel_ev.set()

    t = threading.Thread(target=cancel_soon, daemon=True)
    t.start()
    with pytest.raises(HumanApprovalRequired):
        agent.run("Context", wait_event=wait_ev, cancel_event=cancel_ev)
    t.join(timeout=2)


def test_human_agent_wait_event_timeout():
    """Timeout during wait raises HumanGateTimeout."""
    agent = HumanAgent("human_pm", agent_config={"auto_approve": False})
    wait_ev = threading.Event()

    with patch(
        "backend.App.orchestration.infrastructure.agents.human_agent._human_gate_timeout_sec",
        return_value=0.1,  # Very short timeout
    ):
        with pytest.raises(HumanGateTimeout):
            agent.run("Context", wait_event=wait_ev)


# ---------------------------------------------------------------------------
# HumanAgent — metadata attributes
# ---------------------------------------------------------------------------

def test_human_agent_used_provider():
    agent = HumanAgent("human_pm")
    assert agent.used_provider == "human"
    assert agent.used_model == ""


def test_human_agent_step_attribute():
    agent = HumanAgent("human_qa")
    assert agent.step == "human_qa"

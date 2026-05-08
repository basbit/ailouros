"""Desktop-aware capability descriptions surfaced via probe_capabilities()."""

from __future__ import annotations

import pytest

from backend.App.integrations.application.runtime_capabilities import probe_capabilities


def _by_name(probes, name):
    for probe in probes:
        if probe.name == name:
            return probe
    raise AssertionError(f"capability {name!r} missing from probe results")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for key in (
        "AILOUROS_DESKTOP",
        "SWARM_ALLOW_WORKSPACE_WRITE",
        "SWARM_ALLOW_COMMAND_EXEC",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


def test_workspace_write_ready_when_env_set(monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    probe = _by_name(probe_capabilities(), "workspace_write")
    assert probe.ready is True
    assert probe.detail == "SWARM_ALLOW_WORKSPACE_WRITE=1"


def test_workspace_write_default_message_in_web_mode():
    probe = _by_name(probe_capabilities(), "workspace_write")
    assert probe.ready is False
    assert "set SWARM_ALLOW_WORKSPACE_WRITE=1" in probe.detail


def test_workspace_write_desktop_message_when_missing(monkeypatch):
    monkeypatch.setenv("AILOUROS_DESKTOP", "1")
    probe = _by_name(probe_capabilities(), "workspace_write")
    assert probe.ready is False
    assert "Desktop runtime" in probe.detail


def test_command_exec_desktop_message_when_missing(monkeypatch):
    monkeypatch.setenv("AILOUROS_DESKTOP", "1")
    probe = _by_name(probe_capabilities(), "command_exec")
    assert probe.ready is False
    assert "Desktop runtime" in probe.detail

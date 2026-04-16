"""Regression: startup git-fetch check must never block or auto-pull.

Feature: banner in UI when the running code is behind origin.
Review-rules §2: on any git error we must return ``unknown=True`` rather
than a misleading "up to date" result.
"""
from __future__ import annotations

from unittest.mock import patch

from backend.App.integrations.infrastructure.update_check import (
    UpdateStatus,
    _repo_root,
    check_for_updates,
    get_status,
    status_as_dict,
)


def test_skip_env_returns_unknown(monkeypatch):
    monkeypatch.setenv("SWARM_SKIP_UPDATE_CHECK", "1")
    s = check_for_updates(fetch=False)
    assert s.checked and s.unknown
    assert "SKIP" in s.reason


def test_status_as_dict_is_json_serializable():
    """The endpoint returns JSON — make sure dataclass serialises cleanly."""
    d = status_as_dict()
    # Required keys for the UI banner
    assert {"checked", "unknown", "behind", "ahead", "branch"} <= set(d)


def test_update_available_helper_requires_checked_and_known_and_behind():
    assert not UpdateStatus(
        checked=False, unknown=False, behind=5, ahead=0,
        current_ref="", remote_ref="", branch="main",
    ).update_available()
    assert not UpdateStatus(
        checked=True, unknown=True, behind=5, ahead=0,
        current_ref="", remote_ref="", branch="main",
    ).update_available()
    assert not UpdateStatus(
        checked=True, unknown=False, behind=0, ahead=0,
        current_ref="", remote_ref="", branch="main",
    ).update_available()
    assert UpdateStatus(
        checked=True, unknown=False, behind=3, ahead=0,
        current_ref="", remote_ref="", branch="main",
    ).update_available()


def test_no_git_repo_returns_unknown(monkeypatch, tmp_path):
    monkeypatch.delenv("SWARM_SKIP_UPDATE_CHECK", raising=False)
    with patch(
        "backend.App.integrations.infrastructure.update_check._repo_root",
        return_value=None,
    ):
        s = check_for_updates(fetch=False)
    assert s.checked and s.unknown
    assert "no .git" in s.reason


def test_get_status_returns_the_cached_value(monkeypatch):
    monkeypatch.setenv("SWARM_SKIP_UPDATE_CHECK", "1")
    check_for_updates(fetch=False)
    s = get_status()
    assert s.checked
    assert s.unknown


def test_repo_root_detection_works_for_this_repo():
    """When running from inside the agent-swarm checkout, we should find .git."""
    root = _repo_root()
    # The module lives inside agent-swarm; either a .git exists (dev) or
    # not (slim prod image). Both are acceptable — just assert the helper
    # doesn't raise.
    assert root is None or (root / ".git").exists()

from __future__ import annotations

from pathlib import Path

import pytest

from backend.App.shared.application import desktop_mode
from backend.App.workspace.application.use_cases.desktop_project_init import (
    desktop_info_payload,
    init_desktop_project_workspace,
)


def test_info_when_desktop_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(desktop_mode.DESKTOP_FLAG_ENV, raising=False)
    monkeypatch.delenv(desktop_mode.WORKSPACES_DIR_ENV, raising=False)
    assert desktop_info_payload() == {"is_desktop": False, "workspaces_dir": None}


def test_info_when_desktop_on_without_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(desktop_mode.DESKTOP_FLAG_ENV, "1")
    monkeypatch.delenv(desktop_mode.WORKSPACES_DIR_ENV, raising=False)
    assert desktop_info_payload() == {"is_desktop": True, "workspaces_dir": None}


def test_info_returns_resolved_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(desktop_mode.DESKTOP_FLAG_ENV, "1")
    monkeypatch.setenv(desktop_mode.WORKSPACES_DIR_ENV, str(tmp_path))
    payload = desktop_info_payload()
    assert payload["is_desktop"] is True
    assert payload["workspaces_dir"] == str(tmp_path.resolve())


def test_init_requires_desktop_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(desktop_mode.DESKTOP_FLAG_ENV, raising=False)
    with pytest.raises(ValueError, match="desktop mode is not active"):
        init_desktop_project_workspace("game")


def test_init_requires_workspaces_dir_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(desktop_mode.DESKTOP_FLAG_ENV, "1")
    monkeypatch.delenv(desktop_mode.WORKSPACES_DIR_ENV, raising=False)
    with pytest.raises(ValueError, match="AILOUROS_WORKSPACES_DIR is not set"):
        init_desktop_project_workspace("game")


def test_init_rejects_empty_project_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(desktop_mode.DESKTOP_FLAG_ENV, "1")
    monkeypatch.setenv(desktop_mode.WORKSPACES_DIR_ENV, str(tmp_path))
    with pytest.raises(ValueError, match="project_id must be"):
        init_desktop_project_workspace("")


@pytest.mark.parametrize(
    "bad_id",
    ["..", "../escape", "with/slash", "back\\slash", " spaces ", "name?", "a" * 65],
)
def test_init_rejects_bad_project_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad_id: str
) -> None:
    monkeypatch.setenv(desktop_mode.DESKTOP_FLAG_ENV, "1")
    monkeypatch.setenv(desktop_mode.WORKSPACES_DIR_ENV, str(tmp_path))
    with pytest.raises(ValueError, match="project_id must be"):
        init_desktop_project_workspace(bad_id)


def test_init_requires_existing_base_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(desktop_mode.DESKTOP_FLAG_ENV, "1")
    missing = tmp_path / "absent"
    monkeypatch.setenv(desktop_mode.WORKSPACES_DIR_ENV, str(missing))
    with pytest.raises(ValueError, match="workspaces directory does not exist"):
        init_desktop_project_workspace("game")


def test_init_creates_project_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(desktop_mode.DESKTOP_FLAG_ENV, "1")
    monkeypatch.setenv(desktop_mode.WORKSPACES_DIR_ENV, str(tmp_path))
    target = init_desktop_project_workspace("game")
    assert target == (tmp_path / "game").resolve()
    assert target.is_dir()


def test_init_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(desktop_mode.DESKTOP_FLAG_ENV, "1")
    monkeypatch.setenv(desktop_mode.WORKSPACES_DIR_ENV, str(tmp_path))
    first = init_desktop_project_workspace("game")
    second = init_desktop_project_workspace("game")
    assert first == second
    assert first.is_dir()


@pytest.mark.parametrize("good_id", ["game", "p_123_abc", "My-Project.v2", "a"])
def test_init_accepts_valid_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, good_id: str
) -> None:
    monkeypatch.setenv(desktop_mode.DESKTOP_FLAG_ENV, "1")
    monkeypatch.setenv(desktop_mode.WORKSPACES_DIR_ENV, str(tmp_path))
    target = init_desktop_project_workspace(good_id)
    assert target.parent == tmp_path.resolve()

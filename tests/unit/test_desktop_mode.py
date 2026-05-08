from __future__ import annotations

from pathlib import Path

import pytest

from backend.App.shared.application import desktop_mode


def test_is_desktop_mode_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(desktop_mode.DESKTOP_FLAG_ENV, raising=False)
    assert desktop_mode.is_desktop_mode() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "Yes "])
def test_is_desktop_mode_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(desktop_mode.DESKTOP_FLAG_ENV, value)
    assert desktop_mode.is_desktop_mode() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "garbage"])
def test_is_desktop_mode_falsy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(desktop_mode.DESKTOP_FLAG_ENV, value)
    assert desktop_mode.is_desktop_mode() is False


def test_data_dir_unset_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AILOUROS_DATA_DIR", raising=False)
    assert desktop_mode.desktop_data_dir() is None


def test_data_dir_returns_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AILOUROS_DATA_DIR", str(tmp_path))
    assert desktop_mode.desktop_data_dir() == tmp_path


def test_backend_port_invalid_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(desktop_mode.BACKEND_PORT_ENV, "not-a-number")
    assert desktop_mode.backend_port() is None


def test_backend_port_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(desktop_mode.BACKEND_PORT_ENV, "0")
    assert desktop_mode.backend_port() is None
    monkeypatch.setenv(desktop_mode.BACKEND_PORT_ENV, "70000")
    assert desktop_mode.backend_port() is None


def test_backend_port_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(desktop_mode.BACKEND_PORT_ENV, "18888")
    assert desktop_mode.backend_port() == 18888


def test_factory_desktop_branch_uses_sqlite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(desktop_mode.DESKTOP_FLAG_ENV, "1")
    monkeypatch.setenv("AILOUROS_DATA_DIR", str(tmp_path))

    from backend.App.tasks.infrastructure.task_store_redis import _build_legacy_task_store
    from backend.App.tasks.infrastructure.task_store_sqlite import SqliteTaskStore

    store = _build_legacy_task_store()
    try:
        assert isinstance(store, SqliteTaskStore)
        assert tmp_path in store.db_path().parents
    finally:
        store.close()


def test_factory_non_desktop_branch_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(desktop_mode.DESKTOP_FLAG_ENV, raising=False)
    monkeypatch.setenv("REDIS_REQUIRED", "0")

    from backend.App.tasks.infrastructure.task_store_redis import _build_legacy_task_store
    from backend.App.tasks.infrastructure.task_store_sqlite import SqliteTaskStore

    store = _build_legacy_task_store()
    assert not isinstance(store, SqliteTaskStore)

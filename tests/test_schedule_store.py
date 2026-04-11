"""Tests for backend/App/scheduling/infrastructure/schedule_store.py."""
from __future__ import annotations

import threading
from unittest.mock import patch


def _make_adapter():
    from backend.App.scheduling.infrastructure.schedule_store import ScheduleStoreAdapter
    return ScheduleStoreAdapter()


# ---------------------------------------------------------------------------
# get_job
# ---------------------------------------------------------------------------

def test_get_job_found():
    adapter = _make_adapter()
    lock = threading.Lock()
    store = {"sched-1": {"id": "sched-1", "cron": "0 * * * *"}}
    with patch(
        "backend.UI.REST.controllers.schedules._schedule_store",
        store,
        create=True,
    ), patch(
        "backend.UI.REST.controllers.schedules._schedule_lock",
        lock,
        create=True,
    ):
        result = adapter.get_job("sched-1")
    assert result == {"id": "sched-1", "cron": "0 * * * *"}


def test_get_job_not_found():
    adapter = _make_adapter()
    lock = threading.Lock()
    store = {}
    with patch(
        "backend.UI.REST.controllers.schedules._schedule_store",
        store,
        create=True,
    ), patch(
        "backend.UI.REST.controllers.schedules._schedule_lock",
        lock,
        create=True,
    ):
        result = adapter.get_job("nonexistent")
    assert result is None


def test_get_job_returns_copy():
    """Modifying the returned dict should not affect the store."""
    adapter = _make_adapter()
    lock = threading.Lock()
    store = {"sched-2": {"id": "sched-2", "active": True}}
    with patch(
        "backend.UI.REST.controllers.schedules._schedule_store",
        store,
        create=True,
    ), patch(
        "backend.UI.REST.controllers.schedules._schedule_lock",
        lock,
        create=True,
    ):
        result = adapter.get_job("sched-2")
    result["active"] = False
    assert store["sched-2"]["active"] is True  # Original unmodified


def test_get_job_import_error_raises():
    """ImportError from missing schedules module must propagate — no silent None."""
    import pytest
    adapter = _make_adapter()
    with patch.dict("sys.modules", {"backend.UI.REST.controllers.schedules": None}):
        with pytest.raises((ImportError, ModuleNotFoundError)):
            adapter.get_job("any-id")


# ---------------------------------------------------------------------------
# update_job
# ---------------------------------------------------------------------------

def test_update_job_existing():
    adapter = _make_adapter()
    lock = threading.Lock()
    store = {"sched-3": {"id": "sched-3", "status": "active"}}
    with patch(
        "backend.UI.REST.controllers.schedules._schedule_store",
        store,
        create=True,
    ), patch(
        "backend.UI.REST.controllers.schedules._schedule_lock",
        lock,
        create=True,
    ):
        adapter.update_job("sched-3", status="paused")
    assert store["sched-3"]["status"] == "paused"


def test_update_job_not_existing_no_error():
    adapter = _make_adapter()
    lock = threading.Lock()
    store = {}
    with patch(
        "backend.UI.REST.controllers.schedules._schedule_store",
        store,
        create=True,
    ), patch(
        "backend.UI.REST.controllers.schedules._schedule_lock",
        lock,
        create=True,
    ):
        adapter.update_job("ghost", status="paused")  # No error
    assert "ghost" not in store


def test_update_job_multiple_kwargs():
    adapter = _make_adapter()
    lock = threading.Lock()
    store = {"sched-4": {"id": "sched-4", "status": "active", "last_run": None}}
    with patch(
        "backend.UI.REST.controllers.schedules._schedule_store",
        store,
        create=True,
    ), patch(
        "backend.UI.REST.controllers.schedules._schedule_lock",
        lock,
        create=True,
    ):
        adapter.update_job("sched-4", status="done", last_run="2024-01-01")
    assert store["sched-4"]["status"] == "done"
    assert store["sched-4"]["last_run"] == "2024-01-01"


def test_update_job_import_error_raises():
    """ImportError from missing schedules module must propagate — no silent swallow."""
    import pytest
    adapter = _make_adapter()
    with patch.dict("sys.modules", {"backend.UI.REST.controllers.schedules": None}):
        with pytest.raises((ImportError, ModuleNotFoundError)):
            adapter.update_job("any-id", status="done")


# ---------------------------------------------------------------------------
# list_jobs
# ---------------------------------------------------------------------------

def test_list_jobs_empty():
    adapter = _make_adapter()
    lock = threading.Lock()
    store = {}
    with patch(
        "backend.UI.REST.controllers.schedules._schedule_store",
        store,
        create=True,
    ), patch(
        "backend.UI.REST.controllers.schedules._schedule_lock",
        lock,
        create=True,
    ):
        result = adapter.list_jobs()
    assert result == []


def test_list_jobs_multiple():
    adapter = _make_adapter()
    lock = threading.Lock()
    store = {
        "s1": {"id": "s1", "cron": "* * * * *"},
        "s2": {"id": "s2", "cron": "0 1 * * *"},
    }
    with patch(
        "backend.UI.REST.controllers.schedules._schedule_store",
        store,
        create=True,
    ), patch(
        "backend.UI.REST.controllers.schedules._schedule_lock",
        lock,
        create=True,
    ):
        result = adapter.list_jobs()
    assert len(result) == 2
    ids = {j["id"] for j in result}
    assert ids == {"s1", "s2"}


def test_list_jobs_returns_copies():
    adapter = _make_adapter()
    lock = threading.Lock()
    store = {"s3": {"id": "s3", "status": "active"}}
    with patch(
        "backend.UI.REST.controllers.schedules._schedule_store",
        store,
        create=True,
    ), patch(
        "backend.UI.REST.controllers.schedules._schedule_lock",
        lock,
        create=True,
    ):
        result = adapter.list_jobs()
    result[0]["status"] = "modified"
    assert store["s3"]["status"] == "active"  # Original unmodified


def test_list_jobs_import_error_raises():
    """ImportError from missing schedules module must propagate — no silent empty list."""
    import pytest
    adapter = _make_adapter()
    with patch.dict("sys.modules", {"backend.UI.REST.controllers.schedules": None}):
        with pytest.raises((ImportError, ModuleNotFoundError)):
            adapter.list_jobs()

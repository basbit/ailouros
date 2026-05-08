from __future__ import annotations

from pathlib import Path

import pytest

from backend.App.tasks.infrastructure.task_store_sqlite import SqliteTaskStore


@pytest.fixture()
def store(tmp_path: Path) -> SqliteTaskStore:
    return SqliteTaskStore(db_path=tmp_path / "tasks.sqlite", max_size=10)


def test_rejects_non_positive_max_size(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SqliteTaskStore(db_path=tmp_path / "x.sqlite", max_size=0)


def test_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "deeper"
    SqliteTaskStore(db_path=nested / "tasks.sqlite")
    assert nested.is_dir()


def test_create_returns_payload_shape(store: SqliteTaskStore) -> None:
    payload = store.create_task("hello world")
    assert set(payload) >= {
        "task_id",
        "task",
        "status",
        "agents",
        "history",
        "created_at",
        "updated_at",
        "version",
    }
    assert payload["task"] == "hello world"
    assert payload["status"] == "in_progress"
    assert payload["agents"] == []
    assert payload["history"] == []
    assert payload["version"] == 0


def test_get_unknown_raises_keyerror(store: SqliteTaskStore) -> None:
    with pytest.raises(KeyError):
        store.get_task("nope")


def test_update_appends_agent_and_history(store: SqliteTaskStore) -> None:
    payload = store.create_task("first")
    updated = store.update_task(payload["task_id"], agent="dev", message="working on it")
    assert updated["agents"] == ["dev"]
    assert len(updated["history"]) == 1
    assert updated["history"][0]["agent"] == "dev"
    assert updated["history"][0]["message"] == "working on it"
    assert updated["version"] == 1


def test_update_does_not_duplicate_agent(store: SqliteTaskStore) -> None:
    payload = store.create_task("x")
    store.update_task(payload["task_id"], agent="qa")
    again = store.update_task(payload["task_id"], agent="qa", message="ok")
    assert again["agents"] == ["qa"]


def test_update_status_only(store: SqliteTaskStore) -> None:
    payload = store.create_task("x")
    updated = store.update_task(payload["task_id"], status="completed")
    assert updated["status"] == "completed"


def test_update_unknown_raises_keyerror(store: SqliteTaskStore) -> None:
    with pytest.raises(KeyError):
        store.update_task("missing", status="x")


def test_update_persists_scenario_fields(store: SqliteTaskStore) -> None:
    payload = store.create_task("x")
    updated = store.update_task(
        payload["task_id"],
        scenario_id="code_review",
        scenario_title="Code Review",
        scenario_category="code_quality",
    )
    assert updated["scenario_id"] == "code_review"
    assert updated["scenario_title"] == "Code Review"
    assert updated["scenario_category"] == "code_quality"
    again = store.get_task(payload["task_id"])
    assert again["scenario_id"] == "code_review"


def test_update_omits_scenario_fields_when_not_supplied(store: SqliteTaskStore) -> None:
    payload = store.create_task("x")
    updated = store.update_task(payload["task_id"], status="completed")
    assert "scenario_id" not in updated


def test_delete_is_idempotent(store: SqliteTaskStore) -> None:
    payload = store.create_task("x")
    store.delete_task(payload["task_id"])
    store.delete_task(payload["task_id"])
    with pytest.raises(KeyError):
        store.get_task(payload["task_id"])


def test_persistence_across_instances(tmp_path: Path) -> None:
    db = tmp_path / "tasks.sqlite"
    first = SqliteTaskStore(db_path=db)
    payload = first.create_task("survive me")
    first.close()

    second = SqliteTaskStore(db_path=db)
    revived = second.get_task(payload["task_id"])
    assert revived["task"] == "survive me"


def test_eviction_when_above_max_size(tmp_path: Path) -> None:
    store = SqliteTaskStore(db_path=tmp_path / "tasks.sqlite", max_size=3)
    ids = [store.create_task(f"t{index}")["task_id"] for index in range(5)]
    listed = {payload["task_id"] for payload in store.list_tasks()}
    assert ids[0] not in listed
    assert ids[1] not in listed
    assert ids[2] in listed
    assert ids[3] in listed
    assert ids[4] in listed


def test_list_tasks_returns_newest_first(tmp_path: Path) -> None:
    store = SqliteTaskStore(db_path=tmp_path / "tasks.sqlite")
    first = store.create_task("a")
    second = store.create_task("b")
    listed = store.list_tasks()
    assert listed[0]["task_id"] == second["task_id"]
    assert listed[1]["task_id"] == first["task_id"]

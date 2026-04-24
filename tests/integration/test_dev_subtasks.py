"""Tests for backend/App/orchestration/application/nodes/dev_subtasks.py."""
from __future__ import annotations


from backend.App.orchestration.application.nodes.dev_subtasks import (
    _dev_devops_max_chars,
    _dev_spec_max_chars,
    normalize_dev_qa_tasks_to_count,
    parse_dev_lead_plan,
    parse_dev_qa_task_plan,
    read_dev_qa_task_count_target,
)


# ---------------------------------------------------------------------------
# parse_dev_qa_task_plan
# ---------------------------------------------------------------------------

def test_parse_dev_qa_task_plan_empty():
    assert parse_dev_qa_task_plan("") == []
    assert parse_dev_qa_task_plan(None) == []


def test_parse_dev_qa_task_plan_json_array():
    raw = """```json
[
  {"id": "1", "title": "Setup", "development_scope": "install deps", "testing_scope": "smoke test"}
]
```"""
    tasks = parse_dev_qa_task_plan(raw)
    assert len(tasks) == 1
    assert tasks[0]["id"] == "1"
    assert tasks[0]["title"] == "Setup"


def test_parse_dev_qa_task_plan_plain_json():
    raw = '[{"id": "1", "title": "Task", "development_scope": "do X", "testing_scope": "test X"}]'
    tasks = parse_dev_qa_task_plan(raw)
    assert len(tasks) == 1


def test_parse_dev_qa_task_plan_dict_with_tasks_key():
    raw = '{"tasks": [{"id": "1", "title": "A", "development_scope": "", "testing_scope": ""}]}'
    tasks = parse_dev_qa_task_plan(raw)
    assert len(tasks) == 1
    assert tasks[0]["title"] == "A"


def test_parse_dev_lead_plan_extracts_deliverables():
    raw = """```json
{
  "tasks": [
    {"id": "1", "title": "Setup", "development_scope": "install deps", "testing_scope": "smoke", "expected_paths": ["src/app.py"], "dependencies": []}
  ],
  "deliverables": {
    "must_exist_files": ["src/app.py", "src/app.py"],
    "spec_symbols": ["AppService", "AppService"],
    "verification_commands": [{"command": "build_gate", "expected": "build gate passes"}],
    "assumptions": ["Redis is available"],
    "production_paths": ["src", "src"],
    "placeholder_allow_list": [{"path": "src/generated", "pattern": "\\\\bTODO\\\\b", "reason": "generated file"}]
  }
}
```"""
    plan = parse_dev_lead_plan(raw)
    assert len(plan["tasks"]) == 1
    assert plan["tasks"][0]["expected_paths"] == ["src/app.py"]
    assert plan["tasks"][0]["dependencies"] == []
    assert plan["deliverables"]["must_exist_files"] == ["src/app.py"]
    assert plan["deliverables"]["spec_symbols"] == ["AppService"]
    assert plan["deliverables"]["verification_commands"] == [{"command": "build_gate", "expected": "build gate passes"}]
    assert plan["deliverables"]["assumptions"] == ["Redis is available"]
    assert plan["deliverables"]["production_paths"] == ["src"]
    assert plan["deliverables"]["placeholder_allow_list"] == [
        {"path": "src/generated", "pattern": "\\bTODO\\b", "reason": "generated file"}
    ]
    assert plan["has_complete_deliverables"] is True


def test_parse_dev_lead_plan_backwards_compatible_array():
    raw = """```json
[
  {"id": "1", "title": "Task", "development_scope": "do X", "testing_scope": "test X"}
]
```"""
    plan = parse_dev_lead_plan(raw)
    assert len(plan["tasks"]) == 1
    assert plan["deliverables"] == {
        "must_exist_files": [],
        "spec_symbols": [],
        "verification_commands": [],
        "assumptions": [],
        "production_paths": [],
        "placeholder_allow_list": [],
    }
    assert plan["has_deliverables"] is False
    assert plan["has_complete_deliverables"] is False


def test_parse_dev_lead_plan_marks_present_deliverables():
    raw = '{"tasks": [{"id": "1", "title": "A", "development_scope": "", "testing_scope": ""}], "deliverables": {}}'
    plan = parse_dev_lead_plan(raw)
    assert plan["has_deliverables"] is True
    assert plan["has_complete_deliverables"] is False


def test_parse_dev_qa_task_plan_no_id_defaults():
    raw = '[{"title": "My Task", "development_scope": "build", "testing_scope": "test"}]'
    tasks = parse_dev_qa_task_plan(raw)
    assert len(tasks) == 1
    assert tasks[0]["id"] == "1"  # defaults to index+1


def test_parse_dev_qa_task_plan_multiple():
    raw = """```json
[
  {"id": "1", "title": "A", "development_scope": "scope1", "testing_scope": "test1"},
  {"id": "2", "title": "B", "development_scope": "scope2", "testing_scope": "test2"}
]
```"""
    tasks = parse_dev_qa_task_plan(raw)
    assert len(tasks) == 2


def test_parse_dev_qa_task_plan_invalid_json():
    assert parse_dev_qa_task_plan("not json at all") == []


def test_parse_dev_qa_task_plan_alternative_field_names():
    # development / testing aliases
    raw = '[{"id": "1", "title": "T", "development": "code it", "testing": "test it"}]'
    tasks = parse_dev_qa_task_plan(raw)
    assert len(tasks) == 1
    assert tasks[0]["development_scope"] == "code it"
    assert tasks[0]["testing_scope"] == "test it"


def test_parse_dev_qa_task_plan_extracts_expected_paths_and_dependencies():
    raw = '[{"id": "1", "title": "T", "development_scope": "code it", "testing_scope": "test it", "expected_paths": ["src/app.py", "src/app.py"], "dependencies": ["0", "0"]}]'
    tasks = parse_dev_qa_task_plan(raw)
    assert tasks[0]["expected_paths"] == ["src/app.py"]
    assert tasks[0]["dependencies"] == ["0"]


def test_parse_dev_qa_task_plan_name_alias():
    raw = '[{"name": "My Task", "development_scope": "", "testing_scope": ""}]'
    tasks = parse_dev_qa_task_plan(raw)
    assert tasks[0]["title"] == "My Task"


def test_parse_dev_qa_task_plan_skips_non_dict_items():
    raw = '[{"id": "1", "title": "A", "development_scope": "", "testing_scope": ""}, "not-a-dict"]'
    tasks = parse_dev_qa_task_plan(raw)
    assert len(tasks) == 1


def test_parse_dev_qa_task_plan_non_list():
    raw = '{"id": "1", "title": "single"}'
    tasks = parse_dev_qa_task_plan(raw)
    assert tasks == []


# ---------------------------------------------------------------------------
# read_dev_qa_task_count_target
# ---------------------------------------------------------------------------

def test_read_dev_qa_task_count_target_none():
    result = read_dev_qa_task_count_target({})
    assert result is None


def test_read_dev_qa_task_count_target_legacy():
    ac = {"swarm": {"dev_qa_task_count": 3}}
    assert read_dev_qa_task_count_target(ac) == 3


def test_read_dev_qa_task_count_target_max_cap():
    ac = {"swarm": {"dev_qa_task_count": 100}}
    assert read_dev_qa_task_count_target(ac) == 20  # capped at _MAX_DEV_QA_SUBTASKS


def test_read_dev_qa_task_count_target_dev_count():
    ac = {"swarm": {"dev_task_count": 4}}
    assert read_dev_qa_task_count_target(ac) == 4


def test_read_dev_qa_task_count_target_max_of_dev_qa():
    ac = {"swarm": {"dev_task_count": 3, "qa_task_count": 5}}
    assert read_dev_qa_task_count_target(ac) == 5


def test_read_dev_qa_task_count_target_from_env(monkeypatch):
    monkeypatch.setenv("SWARM_DEV_QA_TASK_COUNT", "3")
    assert read_dev_qa_task_count_target({}) == 3


def test_read_dev_qa_task_count_target_invalid_env(monkeypatch):
    monkeypatch.setenv("SWARM_DEV_QA_TASK_COUNT", "abc")
    assert read_dev_qa_task_count_target({}) is None


def test_read_dev_qa_task_count_target_zero_invalid():
    ac = {"swarm": {"dev_qa_task_count": 0}}
    assert read_dev_qa_task_count_target(ac) is None


def test_read_dev_qa_task_count_target_negative():
    ac = {"swarm": {"dev_qa_task_count": -1}}
    assert read_dev_qa_task_count_target(ac) is None


def test_read_dev_qa_task_count_target_none_swarm():
    assert read_dev_qa_task_count_target(None) is None


def test_read_dev_qa_task_count_target_swarm_not_dict():
    ac = {"swarm": "not-a-dict"}
    assert read_dev_qa_task_count_target(ac) is None


# ---------------------------------------------------------------------------
# normalize_dev_qa_tasks_to_count
# ---------------------------------------------------------------------------

def _make_task(i: int) -> dict:
    return {
        "id": str(i),
        "title": f"Task {i}",
        "development_scope": "",
        "testing_scope": "",
    }


def test_normalize_trims_excess():
    tasks = [_make_task(i) for i in range(5)]
    result = normalize_dev_qa_tasks_to_count(tasks, 3)
    assert len(result) == 3
    assert result[0]["id"] == "0"


def test_normalize_pads_missing():
    tasks = [_make_task(1)]
    result = normalize_dev_qa_tasks_to_count(tasks, 3)
    assert len(result) == 3
    assert result[2]["id"] == "3"
    assert "Subtask 3" in result[2]["title"]


def test_normalize_exact_count():
    tasks = [_make_task(i) for i in range(3)]
    result = normalize_dev_qa_tasks_to_count(tasks, 3)
    assert len(result) == 3


def test_normalize_empty_tasks():
    result = normalize_dev_qa_tasks_to_count([], 2)
    assert len(result) == 2
    assert result[0]["title"] == "Subtask 1/2"


def test_normalize_target_zero_returns_unchanged():
    tasks = [_make_task(1), _make_task(2)]
    result = normalize_dev_qa_tasks_to_count(tasks, 0)
    assert len(result) == 2


def test_normalize_target_negative():
    tasks = [_make_task(1)]
    result = normalize_dev_qa_tasks_to_count(tasks, -1)
    assert len(result) == 1  # unchanged


def test_normalize_padded_tasks_have_scope():
    result = normalize_dev_qa_tasks_to_count([], 2)
    for task in result:
        assert task["development_scope"] != ""
        assert task["testing_scope"] != ""


# ---------------------------------------------------------------------------
# _dev_spec_max_chars / _dev_devops_max_chars
# ---------------------------------------------------------------------------

def test_dev_spec_max_chars_default(monkeypatch):
    monkeypatch.delenv("SWARM_DEV_SPEC_MAX_CHARS", raising=False)
    assert _dev_spec_max_chars() == 80_000


def test_dev_spec_max_chars_custom(monkeypatch):
    monkeypatch.setenv("SWARM_DEV_SPEC_MAX_CHARS", "50000")
    assert _dev_spec_max_chars() == 50_000


def test_dev_spec_max_chars_zero_uses_default(monkeypatch):
    monkeypatch.setenv("SWARM_DEV_SPEC_MAX_CHARS", "0")
    assert _dev_spec_max_chars() == 80_000


def test_dev_devops_max_chars_default(monkeypatch):
    monkeypatch.delenv("SWARM_DEV_DEVOPS_MAX_CHARS", raising=False)
    assert _dev_devops_max_chars() == 20_000


def test_dev_devops_max_chars_custom(monkeypatch):
    monkeypatch.setenv("SWARM_DEV_DEVOPS_MAX_CHARS", "10000")
    assert _dev_devops_max_chars() == 10_000

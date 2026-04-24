"""Contract tests for Dev Lead / Dev strict failure semantics."""

from unittest.mock import patch

import pytest

from backend.App.orchestration.application.nodes.dev import (
    _small_task_missing_path_batches,
    _small_task_profile,
    dev_lead_node,
    dev_node,
)


def test_dev_lead_node_falls_back_to_empty_deliverables_when_object_is_missing():
    state = {
        "agent_config": {},
        "workspace_root": "",
        "input": "task",
        "task_id": "t-1",
        "pm_output": "",
        "devops_output": "",
        "analyze_code_output": "",
        "refactor_plan_output": "",
        "spec_output": "Approved spec",
    }

    with patch(
        "backend.App.orchestration.application.nodes.dev.pipeline_user_task",
        return_value="task",
    ), patch(
        "backend.App.orchestration.application.nodes.dev._spec_for_build_mcp_safe",
        return_value="Approved spec",
    ), patch(
        "backend.App.orchestration.application.nodes.dev._swarm_prompt_prefix",
        return_value="",
    ), patch(
        "backend.App.orchestration.application.nodes.dev._swarm_languages_line",
        return_value="",
    ), patch(
        "backend.App.orchestration.application.nodes.dev._bare_repo_scaffold_instruction",
        return_value="",
    ), patch(
        "backend.App.orchestration.application.nodes.dev.read_dev_qa_task_count_target",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.nodes.dev._validate_agent_boundary",
        side_effect=lambda *args, **kwargs: None,
    ), patch(
        "backend.App.orchestration.application.nodes.dev.DevLeadAgent.run",
        return_value='```json\n[{"id":"1","title":"x","development_scope":"d","testing_scope":"q"}]\n```',
    ):
        result = dev_lead_node(state)

    assert result["dev_qa_tasks"]
    assert result["deliverables_artifact"] == {
        "assumptions": [],
        "must_exist_files": [],
        "placeholder_allow_list": [],
        "production_paths": [],
        "spec_symbols": [],
        "verification_commands": [],
    }
    assert result["must_exist_files"] == []
    assert result["spec_symbols"] == []
    assert result["production_paths"] == []
    assert result["placeholder_allow_list"] == []


def test_dev_lead_node_allows_partial_canonical_deliverables_keys():
    state = {
        "agent_config": {},
        "workspace_root": "",
        "input": "task",
        "task_id": "t-1b",
        "pm_output": "",
        "devops_output": "",
        "analyze_code_output": "",
        "refactor_plan_output": "",
        "spec_output": "Approved spec",
    }

    with patch(
        "backend.App.orchestration.application.nodes.dev.pipeline_user_task",
        return_value="task",
    ), patch(
        "backend.App.orchestration.application.nodes.dev._spec_for_build_mcp_safe",
        return_value="Approved spec",
    ), patch(
        "backend.App.orchestration.application.nodes.dev._swarm_prompt_prefix",
        return_value="",
    ), patch(
        "backend.App.orchestration.application.nodes.dev._swarm_languages_line",
        return_value="",
    ), patch(
        "backend.App.orchestration.application.nodes.dev._bare_repo_scaffold_instruction",
        return_value="",
    ), patch(
        "backend.App.orchestration.application.nodes.dev.read_dev_qa_task_count_target",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.nodes.dev._validate_agent_boundary",
        side_effect=lambda *args, **kwargs: None,
    ), patch(
        "backend.App.orchestration.application.nodes.dev.DevLeadAgent.run",
        return_value=(
            "```json\n"
            '{"tasks":[{"id":"1","title":"x","development_scope":"d","testing_scope":"q"}],'
            '"deliverables":{"must_exist_files":["src/app.py"],"spec_symbols":["AppService"],'
            '"verification_commands":[{"command":"build_gate","expected":"build gate passes"}],"assumptions":[]}}\n'
            "```"
        ),
    ):
        result = dev_lead_node(state)

    assert result["dev_qa_tasks"]
    assert result["must_exist_files"] == ["src/app.py"]
    assert result["spec_symbols"] == ["AppService"]
    assert result["production_paths"] == []
    assert result["placeholder_allow_list"] == []


def test_dev_node_requires_structured_dev_qa_tasks():
    state = {
        "agent_config": {},
        "workspace_root": "",
        "task_id": "t-2",
        "spec_output": "Approved spec",
        "dev_qa_tasks": [],
    }

    with patch(
        "backend.App.orchestration.application.nodes.dev._should_use_mcp_for_workspace",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.nodes.dev._effective_spec_for_build",
        return_value="Approved spec",
    ):
        with pytest.raises(RuntimeError, match="missing canonical dev_qa_tasks"):
            dev_node(state)


def test_small_task_profile_enabled_for_narrow_subtask():
    profile = _small_task_profile(
        {"expected_paths": ["src/a.py", "src/b.py"], "dependencies": ["1"]}
    )
    assert profile["enabled"] is True


def test_small_task_profile_disabled_for_broad_subtask():
    profile = _small_task_profile(
        {
            "expected_paths": ["a", "b", "c"],
            "dependencies": ["1", "2", "3"],
        }
    )
    assert profile["enabled"] is False


def test_small_task_missing_path_batches_splits_to_atomic_paths():
    assert _small_task_missing_path_batches(["a.py", "b.py"]) == [["a.py"], ["b.py"]]

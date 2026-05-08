from pathlib import Path
from typing import Any

import pytest

from backend.App.orchestration.application.enforcement.pre_review_blockers import (
    enforce_pre_review_blockers,
)
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired


@pytest.fixture
def workspace_dir(tmp_path: Path) -> str:
    target = tmp_path / "workspace"
    target.mkdir()
    return str(target)


def _zero_write_state(workspace: str, *, with_human_dev: bool = False) -> dict[str, Any]:
    state: dict[str, Any] = {
        "workspace_root": workspace,
        "workspace_apply_writes": True,
        "workspace_writes": {
            "written": [],
            "patched": [],
            "udiff_applied": [],
            "parsed": 0,
        },
        "dev_mcp_write_count": 0,
    }
    state["_pipeline_step_ids"] = ["dev", "human_dev"] if with_human_dev else ["dev"]
    return state


def _failed_gates_state(workspace: str, *, with_human_dev: bool = False) -> dict[str, Any]:
    state: dict[str, Any] = {
        "workspace_root": workspace,
        "workspace_apply_writes": True,
        "workspace_writes": {
            "written": ["src/a.py"],
            "patched": [],
            "udiff_applied": [],
            "parsed": 1,
        },
        "_failed_trusted_gates": ["source_corruption"],
        "_failed_trusted_gates_summary": "source_corruption: 2 marker(s) detected",
    }
    state["_pipeline_step_ids"] = ["dev", "human_dev"] if with_human_dev else ["dev"]
    return state


def test_zero_writes_raises_runtime_error_without_human_dev(workspace_dir: str) -> None:
    state = _zero_write_state(workspace_dir, with_human_dev=False)
    with pytest.raises(RuntimeError) as info:
        enforce_pre_review_blockers(state)
    assert "0 workspace writes" in str(info.value)


def test_zero_writes_raises_human_approval_when_human_dev_in_pipeline(
    workspace_dir: str,
) -> None:
    state = _zero_write_state(workspace_dir, with_human_dev=True)
    with pytest.raises(HumanApprovalRequired) as info:
        enforce_pre_review_blockers(state)
    assert info.value.resume_pipeline_step == "dev"


def test_failed_trusted_gates_raise_runtime_error_without_human_dev(
    workspace_dir: str,
) -> None:
    state = _failed_gates_state(workspace_dir, with_human_dev=False)
    with pytest.raises(RuntimeError) as info:
        enforce_pre_review_blockers(state)
    assert "trusted verification gates failed" in str(info.value)


def test_failed_trusted_gates_route_to_human_when_available(
    workspace_dir: str,
) -> None:
    state = _failed_gates_state(workspace_dir, with_human_dev=True)
    with pytest.raises(HumanApprovalRequired):
        enforce_pre_review_blockers(state)


def test_no_block_when_writes_present_and_no_failed_gates(workspace_dir: str) -> None:
    state: dict[str, Any] = {
        "workspace_root": workspace_dir,
        "workspace_apply_writes": True,
        "workspace_writes": {
            "written": ["src/a.py"],
            "patched": [],
            "udiff_applied": [],
            "parsed": 1,
        },
        "_pipeline_step_ids": ["dev"],
    }
    enforce_pre_review_blockers(state)


def test_no_block_when_apply_writes_disabled(workspace_dir: str) -> None:
    state: dict[str, Any] = {
        "workspace_root": workspace_dir,
        "workspace_apply_writes": False,
        "workspace_writes": {
            "written": [],
            "patched": [],
            "udiff_applied": [],
            "parsed": 0,
        },
        "_pipeline_step_ids": ["dev"],
    }
    enforce_pre_review_blockers(state)


def test_no_block_when_mcp_writes_present(workspace_dir: str) -> None:
    state: dict[str, Any] = {
        "workspace_root": workspace_dir,
        "workspace_apply_writes": True,
        "workspace_writes": {
            "written": [],
            "patched": [],
            "udiff_applied": [],
            "parsed": 0,
        },
        "dev_mcp_write_count": 3,
        "_pipeline_step_ids": ["dev"],
    }
    enforce_pre_review_blockers(state)


def test_zero_writes_block_disabled_via_setting(
    monkeypatch: pytest.MonkeyPatch, workspace_dir: str,
) -> None:
    monkeypatch.setenv("SWARM_REQUIRE_DEV_WRITES", "0")
    state = _zero_write_state(workspace_dir, with_human_dev=False)
    enforce_pre_review_blockers(state)


def test_trusted_gates_block_disabled_via_setting(
    monkeypatch: pytest.MonkeyPatch, workspace_dir: str,
) -> None:
    monkeypatch.setenv("SWARM_REQUIRE_TRUSTED_GATES_PASS", "0")
    state = _failed_gates_state(workspace_dir, with_human_dev=False)
    enforce_pre_review_blockers(state)

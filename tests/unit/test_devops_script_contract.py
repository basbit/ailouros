from pathlib import Path
from typing import Any

import pytest

from backend.App.orchestration.application.enforcement.devops_script_contract import (
    enforce_devops_script_contract,
    evaluate_devops_script_contract,
)


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "package.json").write_text(
        '{"scripts":{"test":"vitest"}}\n', encoding="utf-8"
    )
    (workspace / "src").mkdir()
    (workspace / "src" / "index.ts").write_text("export const a = 1;\n", encoding="utf-8")
    return workspace


def test_script_with_repo_reference_passes(workspace_dir: Path) -> None:
    script_path = workspace_dir / "build.sh"
    script_path.write_text(
        "#!/bin/bash\nset -euo pipefail\nnpm run test\n",
        encoding="utf-8",
    )
    state: dict[str, Any] = {
        "workspace_root": str(workspace_dir),
        "workspace_writes": {
            "written": ["build.sh"],
            "patched": [],
            "udiff_applied": [],
        },
    }
    findings = evaluate_devops_script_contract(state)
    assert findings == []


def test_script_without_repo_reference_is_flagged(workspace_dir: Path) -> None:
    script_path = workspace_dir / "build.sh"
    script_path.write_text(
        "#!/bin/bash\necho 'build the project'\necho 'launch app'\n",
        encoding="utf-8",
    )
    state: dict[str, Any] = {
        "workspace_root": str(workspace_dir),
        "workspace_writes": {
            "written": ["build.sh"],
            "patched": [],
            "udiff_applied": [],
        },
    }
    findings = evaluate_devops_script_contract(state)
    assert len(findings) == 1
    assert findings[0].path == "build.sh"
    assert "build" in findings[0].verbs
    assert "launch" in findings[0].verbs


def test_workflow_yaml_in_github_dir_is_evaluated(workspace_dir: Path) -> None:
    workflow_dir = workspace_dir / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        "name: ci\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
        "    steps: [{run: 'echo build the app'}, {run: 'echo deploy'}]\n",
        encoding="utf-8",
    )
    state: dict[str, Any] = {
        "workspace_root": str(workspace_dir),
        "workspace_writes": {
            "written": [".github/workflows/ci.yml"],
            "patched": [],
            "udiff_applied": [],
        },
    }
    findings = evaluate_devops_script_contract(state)
    assert len(findings) == 1
    assert findings[0].path == ".github/workflows/ci.yml"


def test_script_without_runnable_verbs_is_skipped(workspace_dir: Path) -> None:
    script_path = workspace_dir / "notes.sh"
    script_path.write_text(
        "#!/bin/bash\necho 'just a comment about layout'\n",
        encoding="utf-8",
    )
    state: dict[str, Any] = {
        "workspace_root": str(workspace_dir),
        "workspace_writes": {
            "written": ["notes.sh"],
            "patched": [],
            "udiff_applied": [],
        },
    }
    findings = evaluate_devops_script_contract(state)
    assert findings == []


def test_non_script_files_are_skipped(workspace_dir: Path) -> None:
    (workspace_dir / "README.md").write_text("# build the app\nlaunch it\n", encoding="utf-8")
    state: dict[str, Any] = {
        "workspace_root": str(workspace_dir),
        "workspace_writes": {
            "written": ["README.md"],
            "patched": [],
            "udiff_applied": [],
        },
    }
    findings = evaluate_devops_script_contract(state)
    assert findings == []


def test_enforce_records_failure_into_failed_trusted_gates(
    workspace_dir: Path,
) -> None:
    script_path = workspace_dir / "build.sh"
    script_path.write_text(
        "#!/bin/bash\necho 'build the project'\n",
        encoding="utf-8",
    )
    state: dict[str, Any] = {
        "workspace_root": str(workspace_dir),
        "workspace_writes": {
            "written": ["build.sh"],
            "patched": [],
            "udiff_applied": [],
        },
    }
    enforce_devops_script_contract(state)
    failed = state.get("_failed_trusted_gates")
    assert isinstance(failed, list) and "devops_script_contract" in failed
    summary = state.get("_failed_trusted_gates_summary")
    assert isinstance(summary, str) and "build.sh" in summary
    unverified = state.get("devops_unverified_claims")
    assert isinstance(unverified, list) and any(
        "build.sh" in entry for entry in unverified
    )


def test_enforce_with_no_workspace_returns_quietly() -> None:
    state: dict[str, Any] = {"workspace_root": "", "workspace_writes": {}}
    enforce_devops_script_contract(state)
    assert "_failed_trusted_gates" not in state


def test_enforce_with_no_writes_returns_quietly(workspace_dir: Path) -> None:
    state: dict[str, Any] = {
        "workspace_root": str(workspace_dir),
        "workspace_writes": {"written": [], "patched": [], "udiff_applied": []},
    }
    enforce_devops_script_contract(state)
    assert "_failed_trusted_gates" not in state

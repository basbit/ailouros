"""Tests for backend/App/orchestration/application/nodes/_workspace_instructions.py."""
from unittest.mock import patch

import pytest

from backend.App.orchestration.application.nodes._workspace_instructions import (
    _bare_repo_scaffold_instruction,
    _dev_workspace_instructions,
    _path_hints_automated_tests,
    _qa_workspace_verification_instructions,
    _workspace_root_str,
)


def _state(**kwargs):
    return dict(kwargs)


# ---------------------------------------------------------------------------
# _workspace_root_str
# ---------------------------------------------------------------------------

def test_workspace_root_str_returns_stripped():
    state = _state(workspace_root="  /proj  ")
    assert _workspace_root_str(state) == "/proj"


def test_workspace_root_str_empty():
    assert _workspace_root_str({}) == ""


# ---------------------------------------------------------------------------
# _path_hints_automated_tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("src/utils.test.ts", True),
    ("src/utils.spec.js", True),
    ("tests/test_foo.py", True),
    ("__tests__/bar.js", True),
    ("spec/feature.rb", True),
    ("test_helper.py", True),
    ("foo_test.go", True),
    ("src/utils.py", False),
    ("lib/helper.js", False),
])
def test_path_hints_automated_tests(path, expected):
    assert _path_hints_automated_tests(path) == expected


def test_path_hints_backslash_path():
    assert _path_hints_automated_tests("src\\utils.test.ts") is True


# ---------------------------------------------------------------------------
# _bare_repo_scaffold_instruction
# ---------------------------------------------------------------------------

def test_bare_repo_scaffold_no_workspace_root():
    state = _state(workspace_apply_writes=True)
    assert _bare_repo_scaffold_instruction(state) == ""


def test_bare_repo_scaffold_no_apply_writes():
    state = _state(workspace_root="/proj", workspace_apply_writes=False)
    assert _bare_repo_scaffold_instruction(state) == ""


def test_bare_repo_scaffold_no_code_analysis():
    state = _state(workspace_root="/proj", workspace_apply_writes=True)
    assert _bare_repo_scaffold_instruction(state) == ""


def test_bare_repo_scaffold_empty_files():
    state = _state(workspace_root="/proj", workspace_apply_writes=True,
                   code_analysis={"files": []})
    assert _bare_repo_scaffold_instruction(state) == ""


def test_bare_repo_scaffold_has_test_files():
    state = _state(
        workspace_root="/proj", workspace_apply_writes=True,
        code_analysis={"files": [{"path": "tests/test_foo.py"}, {"path": "src/app.py"}]},
    )
    assert _bare_repo_scaffold_instruction(state) == ""


def test_bare_repo_scaffold_no_test_files():
    state = _state(
        workspace_root="/proj", workspace_apply_writes=True,
        code_analysis={"files": [{"path": "src/app.py"}, {"path": "lib/util.py"}]},
    )
    result = _bare_repo_scaffold_instruction(state)
    assert "automated test" in result
    assert "swarm_file" in result


# ---------------------------------------------------------------------------
# _dev_workspace_instructions
# ---------------------------------------------------------------------------

def test_dev_workspace_instructions_no_root():
    state = _state()
    assert _dev_workspace_instructions(state) == ""


def test_dev_workspace_instructions_with_root_writes_disabled():
    state = _state(workspace_root="/proj", workspace_apply_writes=False)
    with patch(
        "backend.App.workspace.infrastructure.workspace_io.command_exec_allowed",
        return_value=False,
    ):
        result = _dev_workspace_instructions(state)
    assert "/proj" in result
    assert "DISABLED" in result


def test_dev_workspace_instructions_writes_enabled():
    state = _state(workspace_root="/proj", workspace_apply_writes=True)
    with patch(
        "backend.App.workspace.infrastructure.workspace_io.command_exec_allowed",
        return_value=False,
    ):
        result = _dev_workspace_instructions(state)
    assert "ENABLED" in result
    assert "swarm_file" in result


def test_dev_workspace_instructions_cmd_exec_enabled():
    state = _state(workspace_root="/proj", workspace_apply_writes=True)
    with patch(
        "backend.App.workspace.infrastructure.workspace_io.command_exec_allowed",
        return_value=True,
    ):
        result = _dev_workspace_instructions(state)
    assert "Command execution ENABLED" in result


def test_dev_workspace_instructions_cmd_exec_disabled():
    state = _state(workspace_root="/proj", workspace_apply_writes=False)
    with patch(
        "backend.App.workspace.infrastructure.workspace_io.command_exec_allowed",
        return_value=False,
    ):
        result = _dev_workspace_instructions(state)
    assert "DISABLED" in result


def test_dev_workspace_instructions_import_error_for_command_exec():
    state = _state(workspace_root="/proj", workspace_apply_writes=False)
    # Simulate exception during command_exec_allowed import
    with patch(
        "backend.App.workspace.infrastructure.workspace_io.command_exec_allowed",
        side_effect=Exception("no module"),
    ):
        result = _dev_workspace_instructions(state)
    # Should still work, cmd_exec defaults to False
    assert "/proj" in result


# ---------------------------------------------------------------------------
# _qa_workspace_verification_instructions
# ---------------------------------------------------------------------------

def test_qa_workspace_instructions_no_root():
    state = _state()
    assert _qa_workspace_verification_instructions(state) == ""


def test_qa_workspace_instructions_disabled():
    state = _state(workspace_root="/proj", workspace_apply_writes=False)
    with patch(
        "backend.App.workspace.infrastructure.workspace_io.command_exec_allowed",
        return_value=False,
    ):
        result = _qa_workspace_verification_instructions(state)
    assert "workspace_write" in result
    assert "disabled" in result


def test_qa_workspace_instructions_writes_only():
    state = _state(workspace_root="/proj", workspace_apply_writes=True)
    with patch(
        "backend.App.workspace.infrastructure.workspace_io.command_exec_allowed",
        return_value=False,
    ):
        result = _qa_workspace_verification_instructions(state)
    assert "swarm_file" in result


def test_qa_workspace_instructions_writes_and_cmd_exec():
    state = _state(workspace_root="/proj", workspace_apply_writes=True)
    with patch(
        "backend.App.workspace.infrastructure.workspace_io.command_exec_allowed",
        return_value=True,
    ):
        result = _qa_workspace_verification_instructions(state)
    assert "swarm_shell" in result
    assert "SWARM_ALLOW_COMMAND_EXEC" in result

"""E2E test: pipeline output with <swarm_file> tags → files written to workspace.

Verifies the complete chain: agent output → patch_parser → file on disk.
This is the BUG-3 acceptance test.
"""
from __future__ import annotations

from pathlib import Path

from backend.App.workspace.infrastructure.patch_parser import (
    apply_from_devops_and_dev_outputs,
    text_contains_swarm_workspace_actions,
)


def test_swarm_file_tag_creates_file(tmp_path: Path):
    """Dev output with <swarm_file> tag → file appears in workspace."""
    workspace = tmp_path / "project"
    workspace.mkdir()

    dev_output = (
        "Here is the implementation:\n\n"
        '<swarm_file path="src/hello.py">\n'
        'def hello():\n'
        '    return "Hello, world!"\n'
        "</swarm_file>\n"
    )

    state = {"dev_output": dev_output}
    assert text_contains_swarm_workspace_actions(dev_output)

    result = apply_from_devops_and_dev_outputs(state, workspace)
    written = result.get("written") or []
    assert len(written) >= 1, f"Expected at least 1 file written, got: {result}"

    hello_file = workspace / "src" / "hello.py"
    assert hello_file.is_file(), f"Expected {hello_file} to exist"
    content = hello_file.read_text()
    assert 'def hello()' in content


def test_multiple_swarm_files(tmp_path: Path):
    """Multiple <swarm_file> tags → multiple files created."""
    workspace = tmp_path / "project"
    workspace.mkdir()

    dev_output = (
        '<swarm_file path="a.py">print("a")</swarm_file>\n'
        '<swarm_file path="b.py">print("b")</swarm_file>\n'
    )

    state = {"dev_output": dev_output}
    result = apply_from_devops_and_dev_outputs(state, workspace)
    written = result.get("written") or []
    assert len(written) >= 2

    assert (workspace / "a.py").read_text().strip() == 'print("a")'
    assert (workspace / "b.py").read_text().strip() == 'print("b")'


def test_devops_and_dev_outputs_combined(tmp_path: Path):
    """Both devops_output and dev_output are processed."""
    workspace = tmp_path / "project"
    workspace.mkdir()

    state = {
        "devops_output": '<swarm_file path="Dockerfile">FROM python:3.9</swarm_file>',
        "dev_output": '<swarm_file path="main.py">print("main")</swarm_file>',
    }
    result = apply_from_devops_and_dev_outputs(state, workspace)
    written = result.get("written") or []
    assert len(written) >= 2
    assert (workspace / "Dockerfile").is_file()
    assert (workspace / "main.py").is_file()


def test_no_swarm_tags_no_files(tmp_path: Path):
    """Output without swarm tags → zero files written."""
    workspace = tmp_path / "project"
    workspace.mkdir()

    state = {"dev_output": "Here is the plan:\n1. Do this\n2. Do that"}
    result = apply_from_devops_and_dev_outputs(state, workspace)
    written = result.get("written") or []
    assert len(written) == 0


def test_dry_run_does_not_write(tmp_path: Path):
    """dry_run=True → files are not actually created."""
    workspace = tmp_path / "project"
    workspace.mkdir()

    state = {"dev_output": '<swarm_file path="x.py">code</swarm_file>'}
    apply_from_devops_and_dev_outputs(state, workspace, dry_run=True)
    assert not (workspace / "x.py").exists()


def test_nested_directory_creation(tmp_path: Path):
    """<swarm_file> with nested path → directories auto-created."""
    workspace = tmp_path / "project"
    workspace.mkdir()

    state = {
        "dev_output": '<swarm_file path="deep/nested/dir/file.txt">content</swarm_file>',
    }
    result = apply_from_devops_and_dev_outputs(state, workspace)
    written = result.get("written") or []
    assert len(written) >= 1
    assert (workspace / "deep" / "nested" / "dir" / "file.txt").is_file()

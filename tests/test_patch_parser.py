"""Tests for backend/App/workspace/infrastructure/patch_parser.py."""
from __future__ import annotations


import pytest

from backend.App.workspace.infrastructure.patch_parser import (
    _extract_commands_from_bare_bash_fences,
    _is_placeholder_swarm_file_body,
    _strip_outer_markdown_fence_from_swarm_file_body,
    any_snapshot_output_has_swarm,
    apply_workspace_pipeline,
    apply_workspace_writes,
    collect_workspace_source_chunks,
    extract_shell_commands,
    merged_workspace_source_text,
    parse_fence_file_writes,
    parse_swarm_file_writes,
    safe_relative_path,
    text_contains_swarm_workspace_actions,
)


# ---------------------------------------------------------------------------
# _is_placeholder_swarm_file_body
# ---------------------------------------------------------------------------

def test_placeholder_empty():
    assert _is_placeholder_swarm_file_body("") is True


def test_placeholder_whitespace_only():
    assert _is_placeholder_swarm_file_body("   \n  ") is True


def test_placeholder_dots():
    assert _is_placeholder_swarm_file_body("...") is True


def test_placeholder_single_dot():
    assert _is_placeholder_swarm_file_body(".") is True


def test_placeholder_ellipsis_char():
    assert _is_placeholder_swarm_file_body("…") is True


def test_placeholder_real_content():
    assert _is_placeholder_swarm_file_body("print('hello')") is False


def test_placeholder_long_content():
    assert _is_placeholder_swarm_file_body("x" * 100) is False


# ---------------------------------------------------------------------------
# _strip_outer_markdown_fence_from_swarm_file_body
# ---------------------------------------------------------------------------

def test_strip_fence_python():
    raw = "```python\nprint('hi')\n```"
    result = _strip_outer_markdown_fence_from_swarm_file_body(raw)
    assert result == "print('hi')"


def test_strip_fence_no_fence():
    raw = "just code here"
    result = _strip_outer_markdown_fence_from_swarm_file_body(raw)
    assert result == raw


def test_strip_fence_unclosed():
    raw = "```python\nprint('hi')"
    result = _strip_outer_markdown_fence_from_swarm_file_body(raw)
    assert result == raw  # no closing fence → keep original


def test_strip_fence_empty_inner():
    raw = "```python\n```"
    result = _strip_outer_markdown_fence_from_swarm_file_body(raw)
    # empty inner → keep original
    assert result == raw


def test_strip_fence_no_newline():
    raw = "```singleline```"
    result = _strip_outer_markdown_fence_from_swarm_file_body(raw)
    assert result == raw


# ---------------------------------------------------------------------------
# parse_fence_file_writes
# ---------------------------------------------------------------------------

def test_parse_fence_file_writes_empty():
    assert parse_fence_file_writes("") == []


def test_parse_fence_file_writes_comment_style():
    text = '<!-- SWARM_FILE path="src/app.py" -->\n```python\nprint("hello")\n```'
    result = parse_fence_file_writes(text)
    assert len(result) == 1
    assert result[0][1] == "src/app.py"
    assert "print" in result[0][2]


def test_parse_fence_file_writes_path_line_style():
    text = "```python src/utils.py\ndef foo(): pass\n```"
    result = parse_fence_file_writes(text)
    assert len(result) == 1
    assert result[0][1] == "src/utils.py"


def test_parse_fence_file_writes_traversal_rejected():
    text = '<!-- SWARM_FILE path="../evil.py" -->\n```python\nbad code\n```'
    result = parse_fence_file_writes(text)
    assert len(result) == 0


def test_parse_fence_file_writes_no_match():
    text = "Just regular text without any fences"
    assert parse_fence_file_writes(text) == []


# ---------------------------------------------------------------------------
# safe_relative_path
# ---------------------------------------------------------------------------

def test_safe_relative_path_ok(tmp_path):
    result = safe_relative_path(tmp_path, "subdir/file.py")
    assert result == (tmp_path / "subdir" / "file.py").resolve()


def test_safe_relative_path_null_byte(tmp_path):
    with pytest.raises(ValueError, match="null byte"):
        safe_relative_path(tmp_path, "file\x00.py")


def test_safe_relative_path_absolute(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        safe_relative_path(tmp_path, "/etc/passwd")


def test_safe_relative_path_traversal(tmp_path):
    with pytest.raises(ValueError, match="unsafe"):
        safe_relative_path(tmp_path, "../outside.py")


def test_safe_relative_path_backslash(tmp_path):
    result = safe_relative_path(tmp_path, "sub\\file.py")
    assert result == (tmp_path / "sub" / "file.py").resolve()


def test_safe_relative_path_empty(tmp_path):
    with pytest.raises(ValueError):
        safe_relative_path(tmp_path, "")


# ---------------------------------------------------------------------------
# parse_swarm_file_writes
# ---------------------------------------------------------------------------

def test_parse_swarm_file_writes_basic():
    text = '<swarm_file path="a.py">content here</swarm_file>'
    result = parse_swarm_file_writes(text)
    assert len(result) == 1
    assert result[0][0] == "a.py"
    assert result[0][1] == "content here"


def test_parse_swarm_file_writes_multiple():
    text = (
        '<swarm_file path="a.py">first</swarm_file>\n'
        '<swarm_file path="b.py">second</swarm_file>'
    )
    result = parse_swarm_file_writes(text)
    assert len(result) == 2


def test_parse_swarm_file_writes_empty():
    assert parse_swarm_file_writes("no tags here") == []


# ---------------------------------------------------------------------------
# apply_workspace_writes
# ---------------------------------------------------------------------------

def test_apply_workspace_writes_creates_file(tmp_path):
    result = apply_workspace_writes(tmp_path, [("new_file.py", "hello")])
    assert result["errors"] == []
    assert "new_file.py" in result["written"]
    assert result["write_actions"] == [{"path": "new_file.py", "mode": "create_file"}]
    assert (tmp_path / "new_file.py").read_text() == "hello"


def test_apply_workspace_writes_creates_subdirs(tmp_path):
    result = apply_workspace_writes(tmp_path, [("sub/dir/file.py", "code")])
    assert result["errors"] == []
    assert (tmp_path / "sub" / "dir" / "file.py").exists()


def test_apply_workspace_writes_dry_run(tmp_path):
    result = apply_workspace_writes(tmp_path, [("dry.py", "code")], dry_run=True)
    assert "dry.py" in result["written"]
    assert result["write_actions"] == [{"path": "dry.py", "mode": "create_file"}]
    assert not (tmp_path / "dry.py").exists()


def test_apply_workspace_writes_invalid_path(tmp_path):
    result = apply_workspace_writes(tmp_path, [("../escape.py", "code")])
    assert len(result["errors"]) == 1
    assert result["written"] == []


def test_apply_workspace_writes_empty_list(tmp_path):
    result = apply_workspace_writes(tmp_path, [])
    assert result["written"] == []
    assert result["write_actions"] == []
    assert result["errors"] == []


def test_apply_workspace_writes_multiple(tmp_path):
    writes = [("a.py", "aaa"), ("b.py", "bbb")]
    result = apply_workspace_writes(tmp_path, writes)
    assert len(result["written"]) == 2
    assert result["write_actions"] == [
        {"path": "a.py", "mode": "create_file"},
        {"path": "b.py", "mode": "create_file"},
    ]
    assert result["errors"] == []


# ---------------------------------------------------------------------------
# apply_workspace_pipeline
# ---------------------------------------------------------------------------

def test_apply_workspace_pipeline_no_tags(tmp_path):
    result = apply_workspace_pipeline("just text", tmp_path)
    assert result["parsed"] == 0
    assert "note" in result


def test_apply_workspace_pipeline_swarm_file(tmp_path):
    text = '<swarm_file path="out.py">print("hi")</swarm_file>'
    result = apply_workspace_pipeline(text, tmp_path)
    assert result["errors"] == []
    assert "out.py" in result["written"]
    assert result["write_actions"] == [{"path": "out.py", "mode": "create_file"}]
    assert result["parsed"] == 1


def test_apply_workspace_pipeline_placeholder_body(tmp_path):
    text = '<swarm_file path="empty.py">...</swarm_file>'
    result = apply_workspace_pipeline(text, tmp_path)
    # placeholder body should be skipped with an error note
    assert len(result["errors"]) == 1
    assert result["written"] == []


def test_apply_workspace_pipeline_dry_run(tmp_path):
    text = '<swarm_file path="dry.py">code here</swarm_file>'
    result = apply_workspace_pipeline(text, tmp_path, dry_run=True)
    assert "dry.py" in result["written"]
    assert not (tmp_path / "dry.py").exists()


def test_apply_workspace_pipeline_shell_disabled(tmp_path):
    text = "<swarm_shell>npm test</swarm_shell>"
    result = apply_workspace_pipeline(text, tmp_path, run_shell=False)
    assert len(result["shell_runs"]) == 1
    assert result["shell_runs"][0]["skipped"] is True


def test_apply_workspace_pipeline_shell_dry_run(tmp_path):
    text = "<swarm_shell>python --version</swarm_shell>"
    result = apply_workspace_pipeline(text, tmp_path, dry_run=True, run_shell=True)
    assert len(result["shell_runs"]) >= 1


def test_apply_workspace_pipeline_run_shell_from_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARM_ALLOW_COMMAND_EXEC", raising=False)
    text = "<swarm_shell>python --version</swarm_shell>"
    # run_shell=None → reads from env (defaults to False)
    result = apply_workspace_pipeline(text, tmp_path, run_shell=None)
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# text_contains_swarm_workspace_actions
# ---------------------------------------------------------------------------

def test_text_contains_swarm_file():
    assert text_contains_swarm_workspace_actions('<swarm_file path="x.py">') is True


def test_text_contains_swarm_patch():
    assert text_contains_swarm_workspace_actions('<swarm_patch path="x.py">') is True


def test_text_contains_swarm_shell():
    assert text_contains_swarm_workspace_actions("<swarm_shell>cmd</swarm_shell>") is True


def test_text_contains_no_markers():
    assert text_contains_swarm_workspace_actions("regular text") is False


def test_text_contains_swarm_command():
    assert text_contains_swarm_workspace_actions("<swarm-command>cmd</swarm-command>") is True


# ---------------------------------------------------------------------------
# any_snapshot_output_has_swarm
# ---------------------------------------------------------------------------

def test_any_snapshot_output_has_swarm_true():
    state = {"dev_output": '<swarm_file path="x.py">code</swarm_file>'}
    assert any_snapshot_output_has_swarm(state) is True


def test_any_snapshot_output_has_swarm_false():
    state = {"dev_output": "just text without markers"}
    assert any_snapshot_output_has_swarm(state) is False


def test_any_snapshot_output_has_swarm_empty():
    assert any_snapshot_output_has_swarm({}) is False


def test_any_snapshot_output_has_swarm_in_task_outputs():
    state = {"dev_task_outputs": ['<swarm_shell>cmd</swarm_shell>']}
    assert any_snapshot_output_has_swarm(state) is True


def test_any_snapshot_output_has_swarm_qa_task_outputs():
    state = {"qa_task_outputs": ['<swarm_file path="f.py">content</swarm_file>']}
    assert any_snapshot_output_has_swarm(state) is True


def test_any_snapshot_output_has_swarm_non_string_ignored():
    state = {"dev_output": 42}
    assert any_snapshot_output_has_swarm(state) is False


# ---------------------------------------------------------------------------
# collect_workspace_source_chunks
# ---------------------------------------------------------------------------

def test_collect_workspace_source_chunks_pipeline_steps():
    state = {
        "pipeline_steps": ["pm", "dev"],
        "pm_output": "pm result",
        "dev_output": "dev result",
    }
    chunks = collect_workspace_source_chunks(state)
    assert "pm result" in chunks
    assert "dev result" in chunks


def test_collect_workspace_source_chunks_fallback_no_steps():
    state = {"dev_output": "dev code <swarm_file path='x.py'>...</swarm_file>"}
    chunks = collect_workspace_source_chunks(state)
    assert len(chunks) >= 1


def test_collect_workspace_source_chunks_dev_task_outputs():
    state = {"dev_task_outputs": ["task1 output", "task2 output"]}
    chunks = collect_workspace_source_chunks(state)
    assert "task1 output" in chunks
    assert "task2 output" in chunks


def test_collect_workspace_source_chunks_qa_task_outputs():
    state = {"qa_task_outputs": ["qa1", "qa2"]}
    chunks = collect_workspace_source_chunks(state)
    assert "qa1" in chunks


def test_collect_workspace_source_chunks_empty_state():
    assert collect_workspace_source_chunks({}) == []


def test_collect_workspace_source_chunks_skips_empty_outputs():
    state = {"pipeline_steps": ["dev"], "dev_output": "  "}
    chunks = collect_workspace_source_chunks(state)
    assert chunks == []


def test_collect_workspace_source_chunks_extra_output_with_swarm():
    swarm_text = '<swarm_file path="x.py">code</swarm_file>'
    state = {"custom_output": swarm_text}
    chunks = collect_workspace_source_chunks(state)
    assert swarm_text in chunks


def test_collect_workspace_source_chunks_extra_output_without_swarm():
    state = {"custom_output": "just text without any swarm markers"}
    chunks = collect_workspace_source_chunks(state)
    assert len(chunks) == 0


# ---------------------------------------------------------------------------
# merged_workspace_source_text
# ---------------------------------------------------------------------------

def test_merged_workspace_source_text_empty():
    assert merged_workspace_source_text({}) == ""


def test_merged_workspace_source_text_single():
    state = {"dev_output": "some code"}
    result = merged_workspace_source_text(state)
    assert result == "some code"


def test_merged_workspace_source_text_multiple():
    state = {
        "pipeline_steps": ["pm", "dev"],
        "pm_output": "pm out",
        "dev_output": "dev out",
    }
    result = merged_workspace_source_text(state)
    assert "pm out" in result
    assert "dev out" in result


# ---------------------------------------------------------------------------
# extract_shell_commands
# ---------------------------------------------------------------------------

def test_extract_shell_commands_swarm_shell():
    text = "<swarm_shell>python --version</swarm_shell>"
    cmds = extract_shell_commands(text)
    assert "python --version" in cmds


def test_extract_shell_commands_comment_skipped():
    text = "<swarm_shell># just a comment</swarm_shell>"
    cmds = extract_shell_commands(text)
    assert len(cmds) == 0


def test_extract_shell_commands_empty():
    cmds = extract_shell_commands("")
    assert cmds == []


def test_extract_shell_commands_fallback_bash_fence():
    text = "```bash\npython --version\n```"
    cmds = extract_shell_commands(text)
    # fallback should find it
    assert len(cmds) >= 1


def test_extract_shell_commands_disallowed_command():
    text = "<swarm_shell>rm -rf /</swarm_shell>"
    cmds = extract_shell_commands(text)
    assert len(cmds) == 0


# ---------------------------------------------------------------------------
# _extract_commands_from_bare_bash_fences
# ---------------------------------------------------------------------------

def test_extract_commands_from_bare_bash_fences_allowed():
    text = "```bash\npython --version\n```"
    cmds = _extract_commands_from_bare_bash_fences(text)
    assert "python --version" in cmds


def test_extract_commands_from_bare_bash_fences_comment_skipped():
    text = "```bash\n# just a comment\n```"
    cmds = _extract_commands_from_bare_bash_fences(text)
    assert len(cmds) == 0


def test_extract_commands_from_bare_bash_fences_empty_line_skipped():
    text = "```bash\n\n```"
    cmds = _extract_commands_from_bare_bash_fences(text)
    assert len(cmds) == 0


def test_extract_commands_from_bare_bash_fences_swarm_tag_skipped():
    text = "```bash\n<swarm_shell>cmd</swarm_shell>\n```"
    cmds = _extract_commands_from_bare_bash_fences(text)
    assert len(cmds) == 0

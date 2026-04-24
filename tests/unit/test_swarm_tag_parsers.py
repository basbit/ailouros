"""Tests for backend/App/workspace/infrastructure/swarm_tag_parsers.py."""

import pytest

from backend.App.workspace.infrastructure.swarm_tag_parsers import (
    _bash_sh_fence_body_is_only_swarm_shell,
    _collect_ordered_actions,
    _lift_swarm_shell_from_bash_sh_fences,
    _lift_swarm_shell_from_prompt_style_xml_fences,
    _markdown_fence_spans,
    _position_inside_fences,
    _run_shell_block,
    _shell_block_body_from_match,
    parse_swarm_patch_hunks,
)


# ---------------------------------------------------------------------------
# _shell_block_body_from_match
# ---------------------------------------------------------------------------

def test_shell_block_body_from_match_group1():
    import re
    pat = re.compile(r"<swarm_shell>(.*?)</swarm_shell>|<swarm-command>(.*?)</swarm-command>",
                     re.DOTALL | re.IGNORECASE)
    m = pat.search("<swarm_shell>npm test</swarm_shell>")
    assert _shell_block_body_from_match(m) == "npm test"


def test_shell_block_body_from_match_group2():
    import re
    pat = re.compile(r"<swarm_shell>(.*?)</swarm_shell>|<swarm-command>(.*?)</swarm-command>",
                     re.DOTALL | re.IGNORECASE)
    m = pat.search("<swarm-command>python setup.py</swarm-command>")
    assert _shell_block_body_from_match(m) == "python setup.py"


# ---------------------------------------------------------------------------
# _lift_swarm_shell_from_prompt_style_xml_fences
# ---------------------------------------------------------------------------

def test_lift_swarm_shell_from_xml_fence():
    text = "```xml\n<swarm_shell>npm test</swarm_shell>\n```"
    result = _lift_swarm_shell_from_prompt_style_xml_fences(text)
    assert "<swarm_shell>" in result
    assert "```xml" not in result


def test_lift_swarm_shell_from_xml_fence_no_change():
    text = "```python\nprint('hello')\n```"
    result = _lift_swarm_shell_from_prompt_style_xml_fences(text)
    assert result == text


# ---------------------------------------------------------------------------
# _bash_sh_fence_body_is_only_swarm_shell
# ---------------------------------------------------------------------------

def test_bash_sh_fence_only_shell_tags():
    inner = "<swarm_shell>npm test</swarm_shell>"
    assert _bash_sh_fence_body_is_only_swarm_shell(inner) is True


def test_bash_sh_fence_with_comments():
    inner = "# run tests\n<swarm_shell>pytest</swarm_shell>"
    assert _bash_sh_fence_body_is_only_swarm_shell(inner) is True


def test_bash_sh_fence_with_real_commands():
    inner = "rm -rf /tmp\n<swarm_shell>npm test</swarm_shell>"
    assert _bash_sh_fence_body_is_only_swarm_shell(inner) is False


def test_bash_sh_fence_empty():
    assert _bash_sh_fence_body_is_only_swarm_shell("") is True


# ---------------------------------------------------------------------------
# _lift_swarm_shell_from_bash_sh_fences
# ---------------------------------------------------------------------------

def test_lift_swarm_shell_from_bash_fence():
    text = "```bash\n<swarm_shell>npm test</swarm_shell>\n```"
    result = _lift_swarm_shell_from_bash_sh_fences(text)
    assert "<swarm_shell>npm test</swarm_shell>" in result
    assert "```bash" not in result


def test_lift_swarm_shell_from_bash_fence_keeps_real_commands():
    text = "```bash\nnpm install\nnpm test\n```"
    result = _lift_swarm_shell_from_bash_sh_fences(text)
    assert "```bash" in result  # not lifted


def test_lift_swarm_shell_no_fence():
    text = "Just regular text here"
    result = _lift_swarm_shell_from_bash_sh_fences(text)
    assert result == text


def test_lift_swarm_shell_unclosed_fence():
    text = "```bash\n<swarm_shell>cmd</swarm_shell>"
    result = _lift_swarm_shell_from_bash_sh_fences(text)
    # Should handle unclosed fence without crashing
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _markdown_fence_spans
# ---------------------------------------------------------------------------

def test_markdown_fence_spans_basic():
    text = "before ```code``` after ```more``` end"
    spans = _markdown_fence_spans(text)
    assert len(spans) == 2


def test_markdown_fence_spans_no_fences():
    text = "no fences here"
    assert _markdown_fence_spans(text) == []


def test_markdown_fence_spans_single():
    text = "before ```code``` after"
    spans = _markdown_fence_spans(text)
    assert len(spans) == 1


# ---------------------------------------------------------------------------
# _position_inside_fences
# ---------------------------------------------------------------------------

def test_position_inside_fences_true():
    spans = [(10, 30)]
    assert _position_inside_fences(15, spans) is True


def test_position_inside_fences_false():
    spans = [(10, 30)]
    assert _position_inside_fences(5, spans) is False


def test_position_inside_fences_boundary():
    spans = [(10, 30)]
    assert _position_inside_fences(10, spans) is True
    assert _position_inside_fences(30, spans) is False


# ---------------------------------------------------------------------------
# parse_swarm_patch_hunks
# ---------------------------------------------------------------------------

def test_parse_swarm_patch_hunks_basic():
    body = (
        "<<<<<<< SEARCH\n"
        "old line\n"
        "=======\n"
        "new line\n"
        ">>>>>>> REPLACE"
    )
    hunks = parse_swarm_patch_hunks(body)
    assert len(hunks) == 1
    assert "old line" in hunks[0][0]
    assert "new line" in hunks[0][1]


def test_parse_swarm_patch_hunks_multiple():
    body = (
        "<<<<<<< SEARCH\n"
        "old1\n"
        "=======\n"
        "new1\n"
        ">>>>>>> REPLACE\n"
        "<<<<<<< SEARCH\n"
        "old2\n"
        "=======\n"
        "new2\n"
        ">>>>>>> REPLACE"
    )
    hunks = parse_swarm_patch_hunks(body)
    assert len(hunks) == 2


def test_parse_swarm_patch_hunks_empty_body():
    hunks = parse_swarm_patch_hunks("")
    assert hunks == []


def test_parse_swarm_patch_hunks_no_separator_raises():
    body = "<<<<<<< SEARCH\nold line\n>>>>>>> REPLACE"
    with pytest.raises(ValueError, match="======="):
        parse_swarm_patch_hunks(body)


def test_parse_swarm_patch_hunks_no_replace_raises():
    body = "<<<<<<< SEARCH\nold\n=======\nnew"
    with pytest.raises(ValueError, match="REPLACE"):
        parse_swarm_patch_hunks(body)


def test_parse_swarm_patch_hunks_non_empty_body_no_markers():
    body = "some content without markers"
    with pytest.raises(ValueError, match="no.*SEARCH.*REPLACE.*blocks found"):
        parse_swarm_patch_hunks(body)


def test_parse_swarm_patch_hunks_windows_line_endings():
    body = (
        "<<<<<<< SEARCH\r\n"
        "old\r\n"
        "\r\n=======\r\n"
        "new\r\n"
        ">>>>>>> REPLACE"
    )
    # Should not raise — flexible line ending handling
    hunks = parse_swarm_patch_hunks(body)
    assert len(hunks) >= 1


# ---------------------------------------------------------------------------
# _collect_ordered_actions
# ---------------------------------------------------------------------------

def test_collect_ordered_actions_file():
    text = '<swarm_file path="src/app.py">print("hello")</swarm_file>'
    actions = _collect_ordered_actions(text)
    assert len(actions) == 1
    assert actions[0].kind == "file"
    assert actions[0].rel == "src/app.py"


def test_collect_ordered_actions_shell():
    text = "<swarm_shell>\nnpm test\n</swarm_shell>"
    actions = _collect_ordered_actions(text)
    shell_actions = [a for a in actions if a.kind == "shell"]
    assert len(shell_actions) == 1


def test_collect_ordered_actions_multiple_types():
    text = (
        '<swarm_file path="a.py">content</swarm_file>\n'
        "<swarm_shell>npm test</swarm_shell>\n"
        '<swarm_patch path="b.py"><<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE</swarm_patch>'
    )
    actions = _collect_ordered_actions(text)
    kinds = [a.kind for a in actions]
    assert "file" in kinds
    assert "shell" in kinds
    assert "patch" in kinds


def test_collect_ordered_actions_shell_in_fence_ignored():
    text = "```bash\n<swarm_shell>bad command</swarm_shell>\n```"
    actions = _collect_ordered_actions(text)
    # Shell inside ``` fence should be ignored
    shell_actions = [a for a in actions if a.kind == "shell"]
    assert len(shell_actions) == 0


def test_collect_ordered_actions_bash_fence_fallback():
    text = "```bash\nnpm install\nnpm test\n```"
    actions = _collect_ordered_actions(text)
    shell_actions = [a for a in actions if a.kind == "shell"]
    assert len(shell_actions) == 1


def test_collect_ordered_actions_udiff():
    text = '<swarm_udiff path="src/app.py">--- a/src/app.py\n+++ b/src/app.py</swarm_udiff>'
    actions = _collect_ordered_actions(text)
    udiff_actions = [a for a in actions if a.kind == "udiff"]
    assert len(udiff_actions) == 1


def test_collect_ordered_actions_sorted_by_position():
    text = (
        "<swarm_shell>cmd</swarm_shell>\n"
        '<swarm_file path="a.py">content</swarm_file>'
    )
    actions = _collect_ordered_actions(text)
    if len(actions) >= 2:
        assert actions[0].start <= actions[1].start


# ---------------------------------------------------------------------------
# _run_shell_block
# ---------------------------------------------------------------------------

def test_run_shell_block_disabled(tmp_path):
    parsed, runs, errors = _run_shell_block(
        tmp_path, "npm test", dry_run=False, run_shell=False
    )
    assert len(runs) == 1
    assert runs[0]["skipped"] is True
    assert "SWARM_ALLOW_COMMAND_EXEC" in runs[0]["reason"]


def test_run_shell_block_not_allowed_command(tmp_path):
    parsed, runs, errors = _run_shell_block(
        tmp_path, "rm -rf /", dry_run=False, run_shell=True
    )
    assert len(runs) == 1
    assert runs[0]["skipped"] is True
    assert "allowlist" in runs[0]["reason"]


def test_run_shell_block_empty_lines(tmp_path):
    parsed, runs, errors = _run_shell_block(
        tmp_path, "\n\n# comment\n", dry_run=False, run_shell=True
    )
    assert parsed == 0
    assert runs == []
    assert errors == []


def test_run_shell_block_dry_run(tmp_path):
    parsed, runs, errors = _run_shell_block(
        tmp_path, "python --version", dry_run=True, run_shell=True
    )
    assert len(runs) >= 1
    dry_runs = [r for r in runs if r.get("dry_run")]
    assert len(dry_runs) >= 1

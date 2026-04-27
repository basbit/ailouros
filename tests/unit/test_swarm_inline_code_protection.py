from backend.App.workspace.infrastructure.swarm_tag_parsers import (
    _markdown_inline_code_spans,
    _markdown_protected_spans,
    _collect_ordered_actions,
)
from backend.App.workspace.infrastructure.patch_parser import extract_shell_commands


def test_inline_code_span_detected():
    text = "use `<swarm_shell>` tags for commands"
    spans = _markdown_inline_code_spans(text)
    assert len(spans) == 1
    start, end = spans[0]
    assert text[start:end] == "`<swarm_shell>`"


def test_inline_code_inside_fenced_block_ignored():
    text = "before\n```\nuse `inline` here\n```\nafter"
    spans = _markdown_inline_code_spans(text)
    assert spans == []


def test_protected_spans_combine_fences_and_inline():
    text = "use `<swarm_shell>` and ```\nfenced\n```\nlater"
    spans = _markdown_protected_spans(text)
    assert any(text[s:e] == "`<swarm_shell>`" for s, e in spans)
    assert any("fenced" in text[s:e] for s, e in spans)


def test_swarm_shell_inside_inline_code_is_skipped():
    text = (
        "Reviewer says: please ensure to use `<swarm_shell>` tags so the orchestrator "
        "can pick them up. The artifact lacked them. </swarm_shell>"
    )
    actions = _collect_ordered_actions(text)
    shell_actions = [a for a in actions if a.kind == "shell"]
    assert shell_actions == [], (
        "Shell tag mentioned in inline code must NOT trigger a shell action"
    )


def test_real_swarm_shell_outside_inline_code_is_picked_up():
    text = (
        "Here is the build script:\n"
        "<swarm_shell>\n"
        "echo hello\n"
        "ls -la\n"
        "</swarm_shell>\n"
    )
    actions = _collect_ordered_actions(text)
    shell_actions = [a for a in actions if a.kind == "shell"]
    assert len(shell_actions) == 1
    assert "echo hello" in shell_actions[0].body
    assert "ls -la" in shell_actions[0].body


def test_extract_shell_commands_skips_inline_code_mention():
    text = (
        "Use `<swarm_shell>` tags. Real script below:\n"
        "<swarm_shell>\n"
        "ls\n"
        "</swarm_shell>\n"
    )
    commands = extract_shell_commands(text)
    assert commands == ["ls"]

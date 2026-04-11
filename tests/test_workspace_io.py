"""workspace_io: снимок и разбор <swarm_file>."""

from pathlib import Path

import pytest

from backend.App.workspace.infrastructure.workspace_io import (
    WORKSPACE_CONTEXT_MODE_DEFAULT,
    build_input_with_workspace,
    collect_workspace_file_index,
    collect_workspace_priority_snapshot,
    collect_workspace_snapshot,
    normalize_workspace_context_mode,
    resolve_workspace_context_mode,
    validate_workspace_root,
)
from backend.App.workspace.infrastructure.patch_parser import (
    apply_from_agent_output,
    apply_from_devops_and_dev_outputs,
    apply_workspace_pipeline,
    extract_shell_commands,
    parse_fence_file_writes,
    parse_swarm_file_writes,
    safe_relative_path,
)


def test_parse_swarm_file_writes():
    text = """x
<swarm_file path="a/b.txt">
hello
</swarm_file>
"""
    w = parse_swarm_file_writes(text)
    assert len(w) == 1
    assert w[0][0] == "a/b.txt"
    assert "hello" in w[0][1]


def test_safe_relative_path_rejects_traversal(tmp_path: Path):
    root = tmp_path / "r"
    root.mkdir()
    with pytest.raises(ValueError):
        safe_relative_path(root, "../x")


@pytest.mark.parametrize("bad_path", [
    # Classic traversal
    "../secret",
    "../../etc/passwd",
    # Windows-style backslash traversal (normalised to / then checked)
    "..\\..\\etc\\passwd",
    "..\\..\\..\\.\\windows\\system32",
    # Null-byte injection
    "safe\x00/../etc/passwd",
    "file\x00.txt",
    # Deep traversal that escapes after resolve
    "a/b/../../../../../../etc/passwd",
    # Absolute path attempts
    "/etc/passwd",
    "//etc/passwd",
])
def test_safe_relative_path_rejects_dangerous(tmp_path: Path, bad_path: str):
    """TEST-05: safe_relative_path must raise ValueError for all dangerous inputs."""
    root = tmp_path / "workspace"
    root.mkdir()
    with pytest.raises(ValueError, match=r"(?i)(unsafe|escape)"):
        safe_relative_path(root, bad_path)


def test_safe_relative_path_allows_valid(tmp_path: Path):
    """Ensure safe paths are NOT rejected."""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "subdir").mkdir()
    result = safe_relative_path(root, "subdir/file.txt")
    assert str(result).startswith(str(root))


def test_validate_and_collect(tmp_path: Path):
    p = tmp_path / "proj"
    p.mkdir()
    (p / "hi.py").write_text("print(1)\n", encoding="utf-8")
    vr = validate_workspace_root(p)
    assert vr == p.resolve()
    snap, n = collect_workspace_snapshot(vr, max_files=10, max_total_bytes=100_000)
    assert n >= 1
    assert "hi.py" in snap


def test_build_input_manifest_then_snapshot_order():
    out = build_input_with_workspace(
        "fix bug",
        "snapshot",
        manifest="style: tabs",
    )
    assert "Project context (canonical)" in out
    assert "Workspace snapshot" in out
    assert out.index("canonical") < out.index("Workspace snapshot")
    assert "fix bug" in out


def test_build_input_workspace_index_title():
    out = build_input_with_workspace(
        "task",
        "index body",
        workspace_section_title="Workspace index",
    )
    assert "# Workspace index" in out
    assert "## file:" not in out


def test_collect_workspace_file_index_no_file_bodies(tmp_path: Path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "a.py").write_text("x", encoding="utf-8")
    (root / "b.py").write_text("y", encoding="utf-8")
    text, n = collect_workspace_file_index(validate_workspace_root(root), max_paths=10)
    assert n == 2
    assert "a.py" in text and "b.py" in text
    assert "```" not in text


def test_collect_workspace_file_index_skips_large_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SWARM_WORKSPACE_INDEX_SKIP_LARGE_BYTES", "100")
    monkeypatch.setenv("SWARM_WORKSPACE_INDEX_MAX_OUTPUT_CHARS", "unlimited")
    root = tmp_path / "p"
    root.mkdir()
    (root / "small.txt").write_bytes(b"x")
    (root / "big.bin").write_bytes(b"y" * 200)
    text, n = collect_workspace_file_index(validate_workspace_root(root), max_paths=50)
    assert n == 1
    assert "small.txt" in text
    assert "big.bin" not in text
    assert "omitted" in text


def test_collect_workspace_file_index_max_output_chars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SWARM_WORKSPACE_INDEX_SKIP_LARGE_BYTES", "0")
    monkeypatch.setenv("SWARM_WORKSPACE_INDEX_MAX_OUTPUT_CHARS", "380")
    root = tmp_path / "p"
    root.mkdir()
    for i in range(40):
        (root / f"f{i}.py").write_text("x", encoding="utf-8")
    text, _n = collect_workspace_file_index(validate_workspace_root(root), max_paths=100)
    assert "SWARM_WORKSPACE_INDEX_MAX_OUTPUT_CHARS" in text
    assert len(text) < 900


def test_collect_workspace_file_index_skip_suffixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SWARM_WORKSPACE_INDEX_SKIP_SUFFIXES", ".log")
    monkeypatch.setenv("SWARM_WORKSPACE_INDEX_MAX_OUTPUT_CHARS", "unlimited")
    root = tmp_path / "p"
    root.mkdir()
    (root / "a.py").write_text("x", encoding="utf-8")
    (root / "b.log").write_text("log", encoding="utf-8")
    text, n = collect_workspace_file_index(validate_workspace_root(root), max_paths=50)
    assert n == 1
    assert "a.py" in text
    assert "b.log" not in text


def test_priority_snapshot_requires_patterns(tmp_path: Path):
    root = tmp_path / "p"
    root.mkdir()
    with pytest.raises(ValueError, match="priority_paths"):
        collect_workspace_priority_snapshot(validate_workspace_root(root))


def test_priority_snapshot_from_context_file(tmp_path: Path):
    root = tmp_path / "p"
    root.mkdir()
    (root / ".swarm").mkdir()
    (root / ".swarm" / "context.txt").write_text("hello.txt\n", encoding="utf-8")
    (root / "hello.txt").write_text("content", encoding="utf-8")
    text, n = collect_workspace_priority_snapshot(validate_workspace_root(root))
    assert n == 1
    assert "hello.txt" in text
    assert "content" in text


def test_resolve_workspace_context_mode_from_swarm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SWARM_WORKSPACE_CONTEXT_MODE", raising=False)
    assert resolve_workspace_context_mode({"swarm": {"workspace_context_mode": "index_only"}}) == "index_only"
    assert resolve_workspace_context_mode({}) == WORKSPACE_CONTEXT_MODE_DEFAULT
    assert normalize_workspace_context_mode("retrieve") == "retrieve"


def test_apply_from_agent_output(tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    text = '<swarm_file path="n.txt">ok</swarm_file>'
    r = apply_from_agent_output(text, root)
    assert r["parsed"] == 1
    assert (root / "n.txt").read_text(encoding="utf-8") == "ok"
    assert "n.txt" in r["written"]
    assert r["patched"] == []
    assert r["shell_runs"] == []


def test_devops_instruction_placeholder_swarm_file_does_not_clobber(tmp_path: Path):
    """DevOps часто вставляет «формат: <swarm_file path="…">...</swarm_file>» — не затирать Dev."""
    root = tmp_path / "w"
    root.mkdir()
    (root / "src").mkdir(parents=True)
    (root / "src" / "App.tsx").write_text("export const KEEP = 1;\n", encoding="utf-8")
    text = (
        "Запишите в формате <swarm_file path=\"src/App.tsx\">...</swarm_file>.\n"
    )
    r = apply_workspace_pipeline(text, root)
    assert (root / "src" / "App.tsx").read_text(encoding="utf-8") == "export const KEEP = 1;\n"
    assert r["written"] == []
    assert any("skipped" in e for e in r["errors"])


def test_swarm_file_strips_inner_markdown_fence(tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    text = """<swarm_file path="x.tsx">
```tsx
export const x = 1;
```
</swarm_file>"""
    r = apply_workspace_pipeline(text, root)
    assert r["written"] == ["x.tsx"]
    assert (root / "x.tsx").read_text(encoding="utf-8") == "export const x = 1;"


def test_swarm_shell_inside_xml_fence_lifted_then_parsed(tmp_path: Path):
    """Копипаста ```xml + swarm_shell из старого промпта — поднимаем и парсим оба блока."""
    root = tmp_path / "w"
    root.mkdir()
    text = """```xml
<swarm_shell>
npx react-native start
</swarm_shell>
```
after fence
<swarm_shell>
npm install
</swarm_shell>
"""
    r = apply_workspace_pipeline(text, root, run_shell=False)
    assert len(r["shell_runs"]) == 2
    assert r["shell_runs"][0]["cmd"] == "npx react-native start"
    assert r["shell_runs"][1]["cmd"] == "npm install"


def test_extract_shell_commands_lifts_bash_fence_when_only_shell_tags():
    text = """```bash
<swarm_shell>
npm install
</swarm_shell>
```
"""
    assert extract_shell_commands(text) == ["npm install"]


def test_swarm_shell_inside_bash_fence_lifted_when_only_shell_tags(tmp_path: Path):
    """```bash с комментариями и только тегами swarm — поднимаем (модели так почти всегда пишут)."""
    root = tmp_path / "w"
    root.mkdir()
    text = """```bash
# пример
<swarm_shell>
npm install
</swarm_shell>
```
<swarm_shell>
pip install -r requirements.txt
</swarm_shell>
"""
    r = apply_workspace_pipeline(text, root, run_shell=False)
    assert len(r["shell_runs"]) == 2
    assert r["shell_runs"][0]["cmd"] == "npm install"
    assert r["shell_runs"][1]["cmd"] == "pip install -r requirements.txt"


def test_swarm_shell_inside_bash_fence_not_lifted_when_other_shell_text(tmp_path: Path):
    """Посторонний bash в фенсе — не поднимаем; теги внутри ```bash остаются невидимы парсеру."""
    root = tmp_path / "w"
    root.mkdir()
    text = """```bash
echo hi
<swarm_shell>
npm install
</swarm_shell>
```
<swarm_shell>
pip install -r requirements.txt
</swarm_shell>
"""
    r = apply_workspace_pipeline(text, root, run_shell=False)
    assert len(r["shell_runs"]) == 1
    assert r["shell_runs"][0]["cmd"] == "pip install -r requirements.txt"


def test_swarm_command_alias_parsed(tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    text = """<swarm-command>
npm ci
</swarm-command>"""
    r = apply_workspace_pipeline(text, root, run_shell=False)
    assert len(r["shell_runs"]) == 1
    assert r["shell_runs"][0]["cmd"] == "npm ci"


def test_apply_from_devops_and_dev_outputs_merges(tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    state = {
        "devops_output": '<swarm_file path="a.txt">one</swarm_file>',
        "dev_output": '<swarm_file path="a.txt">two</swarm_file>',
    }
    r = apply_from_devops_and_dev_outputs(state, root)
    assert r["parsed"] == 2
    assert (root / "a.txt").read_text(encoding="utf-8") == "two"


def test_apply_from_generate_documentation_output(tmp_path: Path):
    """Документация без dev/devops — раньше swarm_file игнорировались."""
    root = tmp_path / "w"
    root.mkdir()
    state = {
        "generate_documentation_output": '<swarm_file path="docs/x.md"># Hi</swarm_file>',
    }
    r = apply_from_devops_and_dev_outputs(state, root)
    assert "docs/x.md" in r["written"]
    assert (root / "docs" / "x.md").read_text(encoding="utf-8") == "# Hi"


def test_parse_fence_file_comment_and_path_line():
    t1 = """<!-- SWARM_FILE path="app/Foo.tsx" -->
```tsx
export const x = 1;
```
"""
    r1 = parse_fence_file_writes(t1)
    assert len(r1) == 1
    assert r1[0][1] == "app/Foo.tsx"
    assert "export const x" in r1[0][2]

    t2 = """```tsx src/a.tsx
console.log(1);
```"""
    r2 = parse_fence_file_writes(t2)
    assert len(r2) == 1
    assert r2[0][1] == "src/a.tsx"


def test_apply_workspace_ignores_fence_without_swarm_file_tag(tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    text = """<!-- SWARM_FILE path="x/y.md" -->
```markdown
# hi
```
"""
    r = apply_workspace_pipeline(text, root)
    assert r["written"] == []


def test_apply_workspace_prefers_swarm_file_over_fence(tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    text = """<swarm_file path="a.txt">from_tag</swarm_file>
<!-- SWARM_FILE path="b.txt" -->
```text
from_fence
```
"""
    r = apply_workspace_pipeline(text, root)
    assert r["written"] == ["a.txt"]
    assert (root / "a.txt").read_text(encoding="utf-8") == "from_tag"


def test_write_generated_documentation_to_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    root = tmp_path / "repo"
    root.mkdir()
    from backend.App.workspace.application.doc_workspace import write_generated_documentation_to_workspace

    state = {
        "workspace_root": str(root),
        "workspace_apply_writes": True,
        "task_id": "tid",
        "agent_config": {},
    }
    paths = write_generated_documentation_to_workspace(state, "# Full doc\n", "## mermaid only\n")
    assert "docs/swarm/AGENT_SWARM_DOCS.md" in paths
    assert "docs/swarm/DIAGRAMS.md" in paths
    assert (root / "docs" / "swarm" / "AGENT_SWARM_DOCS.md").read_text(encoding="utf-8") == "# Full doc\n"
    assert (root / "docs" / "swarm" / "DIAGRAMS.md").read_text(encoding="utf-8") == "## mermaid only\n"


def test_apply_workspace_uses_pipeline_steps_order_review_wins(tmp_path: Path):
    """Стрим кладёт review_dev_output, а не dev_output — раньше review игнорировался."""
    root = tmp_path / "w"
    root.mkdir()
    state = {
        "pipeline_steps": ["dev", "review_dev"],
        "dev_output": '<swarm_file path="a.txt">first</swarm_file>',
        "review_dev_output": '<swarm_file path="a.txt">second</swarm_file>',
    }
    apply_from_devops_and_dev_outputs(state, root)
    assert (root / "a.txt").read_text(encoding="utf-8") == "second"


def test_apply_workspace_only_review_dev_when_dev_plain_text(tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    state = {
        "pipeline_steps": ["dev", "review_dev"],
        "dev_output": "только markdown без тегов",
        "review_dev_output": '<swarm_file path="fix.py">x=1</swarm_file>',
    }
    apply_from_devops_and_dev_outputs(state, root)
    assert (root / "fix.py").read_text(encoding="utf-8") == "x=1"


def test_swarm_patch_replace_once(tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    (root / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    text = """<swarm_patch path="a.py">
<<<<<<< SEARCH
two
=======
TWO
>>>>>>> REPLACE
</swarm_patch>"""
    r = apply_workspace_pipeline(text, root)
    assert r["patched"] == ["a.py"]
    assert r["write_actions"] == [{"path": "a.py", "mode": "patch_edit"}]
    assert (root / "a.py").read_text(encoding="utf-8") == "one\nTWO\nthree\n"


def test_swarm_patch_create_empty_search(tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    text = """<swarm_patch path="new.txt">
<<<<<<< SEARCH

=======
hello
>>>>>>> REPLACE
</swarm_patch>"""
    r = apply_workspace_pipeline(text, root)
    assert r["patched"] == ["new.txt"]
    assert r["write_actions"] == [{"path": "new.txt", "mode": "patch_create"}]
    assert (root / "new.txt").read_text(encoding="utf-8") == "hello"


def test_swarm_shell_skipped_without_exec_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("SWARM_ALLOW_COMMAND_EXEC", raising=False)
    root = tmp_path / "w"
    root.mkdir()
    text = "<swarm_shell>\npython3 -c \"print(1)\"\n</swarm_shell>"
    r = apply_workspace_pipeline(text, root)
    assert r["parsed"] == 0
    assert any(x.get("skipped") for x in r["shell_runs"])


def test_shell_allowlist_accepts_pytest():
    from backend.App.workspace.infrastructure.workspace_io import _shell_command_allowed

    ok, reason = _shell_command_allowed("pytest -q tests/")
    assert ok, reason


def test_swarm_shell_python_creates_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("SWARM_ALLOW_COMMAND_EXEC", "1")
    root = tmp_path / "w"
    root.mkdir()
    text = (
        "<swarm_shell>\n"
        "python3 -c \"open('out.txt','w',encoding='utf-8').write('x')\"\n"
        "</swarm_shell>"
    )
    r = apply_workspace_pipeline(text, root)
    assert r["parsed"] == 1
    assert (root / "out.txt").read_text(encoding="utf-8") == "x"
    assert r["shell_runs"][0].get("returncode") == 0

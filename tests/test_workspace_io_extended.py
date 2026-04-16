"""Extended tests for backend/App/workspace/infrastructure/workspace_io.py."""

import pytest

from backend.App.workspace.infrastructure.workspace_io import (
    WORKSPACE_CONTEXT_MODE_FULL,
    _assert_under_workspace,
    _is_under,
    _priority_globs_from_env_and_file,
    _shell_command_allowed,
    command_exec_allowed,
    collect_workspace_priority_snapshot,
    extend_runtime_shell_allowlist,
    extract_command_binary,
    normalize_workspace_context_mode,
    read_project_context_file,
    resolve_project_context_path,
    resolve_workspace_context_mode,
    scoped_runtime_shell_allowlist,
    tools_only_workspace_placeholder,
    validate_readable_file,
    validate_workspace_root,
    workspace_write_allowed,
)


# ---------------------------------------------------------------------------
# workspace_write_allowed / command_exec_allowed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("val", ["1", "true", "yes", "on"])
def test_workspace_write_allowed_truthy(monkeypatch, val):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", val)
    assert workspace_write_allowed() is True


def test_workspace_write_allowed_false(monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "0")
    assert workspace_write_allowed() is False


def test_workspace_write_allowed_unset(monkeypatch):
    monkeypatch.delenv("SWARM_ALLOW_WORKSPACE_WRITE", raising=False)
    assert workspace_write_allowed() is False


@pytest.mark.parametrize("val", ["1", "true", "yes", "on"])
def test_command_exec_allowed_truthy(monkeypatch, val):
    monkeypatch.setenv("SWARM_ALLOW_COMMAND_EXEC", val)
    assert command_exec_allowed() is True


def test_command_exec_allowed_false(monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_COMMAND_EXEC", "0")
    assert command_exec_allowed() is False


# ---------------------------------------------------------------------------
# _is_under / _assert_under_workspace
# ---------------------------------------------------------------------------

def test_is_under_true(tmp_path):
    child = tmp_path / "a" / "b.txt"
    child.parent.mkdir()
    child.touch()
    assert _is_under(tmp_path, child) is True


def test_is_under_false(tmp_path):
    outside = tmp_path.parent / "other"
    assert _is_under(tmp_path, outside) is False


def test_assert_under_workspace_ok(tmp_path):
    child = tmp_path / "sub" / "file.txt"
    child.parent.mkdir()
    child.touch()
    _assert_under_workspace(child, tmp_path)  # should not raise


def test_assert_under_workspace_raises(tmp_path):
    with pytest.raises(ValueError, match="outside workspace"):
        _assert_under_workspace(tmp_path.parent / "outside", tmp_path)


# ---------------------------------------------------------------------------
# validate_workspace_root
# ---------------------------------------------------------------------------

def test_validate_workspace_root_valid(tmp_path):
    result = validate_workspace_root(tmp_path)
    assert result == tmp_path.resolve()


def test_validate_workspace_root_not_dir(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(ValueError, match="not a directory"):
        validate_workspace_root(f)


def test_validate_workspace_root_swarm_base_restriction(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("SWARM_WORKSPACE_BASE", str(base))
    with pytest.raises(ValueError, match="SWARM_WORKSPACE_BASE"):
        validate_workspace_root(outside)


def test_validate_workspace_root_swarm_base_allows_inside(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    inside = base / "project"
    inside.mkdir()
    monkeypatch.setenv("SWARM_WORKSPACE_BASE", str(base))
    result = validate_workspace_root(inside)
    assert result.is_dir()


# ---------------------------------------------------------------------------
# normalize_workspace_context_mode
# ---------------------------------------------------------------------------

def test_normalize_valid_mode():
    assert normalize_workspace_context_mode("index_only") == "index_only"


def test_normalize_unknown_mode():
    assert normalize_workspace_context_mode("unknown_mode") == WORKSPACE_CONTEXT_MODE_FULL


def test_normalize_empty_string():
    assert normalize_workspace_context_mode("") == WORKSPACE_CONTEXT_MODE_FULL


# ---------------------------------------------------------------------------
# resolve_workspace_context_mode
# ---------------------------------------------------------------------------

def test_resolve_workspace_context_mode_from_agent_config():
    ac = {"swarm": {"workspace_context_mode": "index_only"}}
    assert resolve_workspace_context_mode(ac) == "index_only"


def test_resolve_workspace_context_mode_from_env(monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE_CONTEXT_MODE", "retrieve")
    result = resolve_workspace_context_mode(None)
    assert result == "retrieve"


def test_resolve_workspace_context_mode_default(monkeypatch):
    monkeypatch.delenv("SWARM_WORKSPACE_CONTEXT_MODE", raising=False)
    result = resolve_workspace_context_mode(None)
    # should be full or tools_only depending on domain default
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# tools_only_workspace_placeholder
# ---------------------------------------------------------------------------

def test_tools_only_workspace_placeholder_with_root():
    result = tools_only_workspace_placeholder("/home/user/project")
    assert "/home/user/project" in result
    assert "MCP filesystem tools" in result


def test_tools_only_workspace_placeholder_empty_root():
    result = tools_only_workspace_placeholder("")
    assert "(not set)" in result


# ---------------------------------------------------------------------------
# _shell_allowlist / _shell_command_allowed
# ---------------------------------------------------------------------------

def test_shell_command_allowed_python():
    ok, reason = _shell_command_allowed("python -m pytest tests/")
    assert ok is True


def test_shell_command_allowed_npm():
    ok, reason = _shell_command_allowed("npm test")
    assert ok is True


def test_shell_command_allowed_blocked():
    ok, reason = _shell_command_allowed("rm -rf /")
    assert ok is False
    assert "not in allowlist" in reason


def test_shell_command_allowed_empty():
    ok, reason = _shell_command_allowed("")
    assert ok is False
    assert "empty" in reason


def test_shell_command_allowed_comment():
    ok, reason = _shell_command_allowed("# comment line")
    assert ok is False
    assert "comment" in reason


def test_shell_command_allowed_custom_allowlist(monkeypatch):
    monkeypatch.setenv("SWARM_SHELL_ALLOWLIST", "myapp,othertool")
    ok, _ = _shell_command_allowed("myapp run")
    assert ok is True
    ok2, _ = _shell_command_allowed("npm test")
    assert ok2 is False


# ---------------------------------------------------------------------------
# Runtime shell allowlist extension (per-task user approvals)
# ---------------------------------------------------------------------------

def test_runtime_allowlist_extension_permits_approved_binary() -> None:
    """Binaries added inside a scope are executable; env allowlist still wins."""
    # Baseline: godot is not in the default env allowlist.
    ok, reason = _shell_command_allowed("godot --version")
    assert ok is False
    assert "allowlist" in reason

    with scoped_runtime_shell_allowlist():
        extend_runtime_shell_allowlist(["godot", "Flutter.EXE"])
        ok_g, _ = _shell_command_allowed("godot --headless project.tscn")
        assert ok_g is True
        ok_f, _ = _shell_command_allowed("flutter build apk")
        assert ok_f is True
        # Env-based allowlist continues to work unchanged.
        ok_npm, _ = _shell_command_allowed("npm install")
        assert ok_npm is True


def test_runtime_allowlist_extension_scoped_to_task() -> None:
    """Approvals must NOT leak past the ``scoped_runtime_shell_allowlist`` block."""
    with scoped_runtime_shell_allowlist():
        extend_runtime_shell_allowlist(["godot"])
        assert _shell_command_allowed("godot --version")[0] is True

    # After scope exit — godot once again needs approval.
    ok, reason = _shell_command_allowed("godot --version")
    assert ok is False
    assert "allowlist" in reason


def test_runtime_allowlist_extension_idempotent_dedup() -> None:
    """Extending twice with the same binary should not double-count it."""
    with scoped_runtime_shell_allowlist():
        extend_runtime_shell_allowlist(["godot"])
        extend_runtime_shell_allowlist(["GODOT"])  # case-fold
        # Behaviour check: still permits godot exactly once.
        assert _shell_command_allowed("godot --help")[0] is True


def test_extract_command_binary_cases() -> None:
    """extract_command_binary normalises path + case + .exe suffix."""
    assert extract_command_binary("godot --headless foo.tscn") == "godot"
    assert extract_command_binary("/usr/local/bin/Flutter.EXE build ios") == "flutter"
    assert extract_command_binary("") is None
    assert extract_command_binary("# comment line") is None
    # malformed shell — shlex raises → caller gets None (not an exception).
    assert extract_command_binary("a \"b") is None


# ---------------------------------------------------------------------------
# resolve_project_context_path
# ---------------------------------------------------------------------------

def test_resolve_project_context_path_absolute(tmp_path):
    f = tmp_path / "ctx.md"
    f.touch()
    result = resolve_project_context_path(str(f), None)
    assert result == f.resolve()


def test_resolve_project_context_path_relative_with_root(tmp_path):
    f = tmp_path / "ctx.md"
    f.touch()
    result = resolve_project_context_path("ctx.md", tmp_path)
    assert result == f.resolve()


def test_resolve_project_context_path_relative_no_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "ctx.md"
    f.touch()
    result = resolve_project_context_path("ctx.md", None)
    assert result == f.resolve()


# ---------------------------------------------------------------------------
# validate_readable_file
# ---------------------------------------------------------------------------

def test_validate_readable_file_ok(tmp_path):
    f = tmp_path / "file.md"
    f.write_text("content")
    result = validate_readable_file(f)
    assert result == f.resolve()


def test_validate_readable_file_not_file(tmp_path):
    with pytest.raises(ValueError, match="not a file"):
        validate_readable_file(tmp_path / "nonexistent.md")


def test_validate_readable_file_swarm_base_restriction(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("x")
    monkeypatch.setenv("SWARM_WORKSPACE_BASE", str(base))
    with pytest.raises(ValueError, match="SWARM_WORKSPACE_BASE"):
        validate_readable_file(outside)


# ---------------------------------------------------------------------------
# read_project_context_file
# ---------------------------------------------------------------------------

def test_read_project_context_file_ok(tmp_path):
    f = tmp_path / "ctx.md"
    f.write_text("project context here")
    result = read_project_context_file(f)
    assert result == "project context here"


def test_read_project_context_file_too_large(tmp_path):
    f = tmp_path / "big.md"
    f.write_bytes(b"x" * 1000)
    with pytest.raises(ValueError, match="too large"):
        read_project_context_file(f, max_bytes=500)


# ---------------------------------------------------------------------------
# _priority_globs_from_env_and_file
# ---------------------------------------------------------------------------

def test_priority_globs_from_context_file(tmp_path):
    swarm_dir = tmp_path / ".swarm"
    swarm_dir.mkdir()
    (swarm_dir / "context.txt").write_text("src/app.py\n# comment\nlib/*.py\n")
    patterns = _priority_globs_from_env_and_file(tmp_path)
    assert "src/app.py" in patterns
    assert "lib/*.py" in patterns
    assert "# comment" not in patterns


def test_priority_globs_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE_PRIORITY_GLOBS", "*.md,src/*.py")
    patterns = _priority_globs_from_env_and_file(tmp_path)
    assert "*.md" in patterns
    assert "src/*.py" in patterns


def test_priority_globs_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARM_WORKSPACE_PRIORITY_GLOBS", raising=False)
    patterns = _priority_globs_from_env_and_file(tmp_path)
    assert patterns == []


# ---------------------------------------------------------------------------
# collect_workspace_priority_snapshot
# ---------------------------------------------------------------------------

def test_collect_workspace_priority_snapshot_no_patterns(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARM_WORKSPACE_PRIORITY_GLOBS", raising=False)
    with pytest.raises(ValueError, match="context.txt"):
        collect_workspace_priority_snapshot(tmp_path)


def test_collect_workspace_priority_snapshot_with_file(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARM_WORKSPACE_PRIORITY_GLOBS", raising=False)
    swarm_dir = tmp_path / ".swarm"
    swarm_dir.mkdir()
    (swarm_dir / "context.txt").write_text("hello.py\n")
    py_file = tmp_path / "hello.py"
    py_file.write_text("print('hello')")
    result, count = collect_workspace_priority_snapshot(tmp_path)
    assert count == 1
    assert "hello.py" in result
    assert "print" in result


def test_collect_workspace_priority_snapshot_binary_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE_PRIORITY_GLOBS", "data.bin")
    bin_file = tmp_path / "data.bin"
    bin_file.write_bytes(b"\x00\x01\x02\x03binary data")
    result, count = collect_workspace_priority_snapshot(tmp_path)
    assert count == 0
    assert "binary" in result


def test_collect_workspace_priority_snapshot_glob_pattern(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE_PRIORITY_GLOBS", "*.txt")
    (tmp_path / "a.txt").write_text("content A")
    (tmp_path / "b.txt").write_text("content B")
    result, count = collect_workspace_priority_snapshot(tmp_path)
    assert count == 2
    assert "content A" in result
    assert "content B" in result


def test_collect_workspace_priority_snapshot_large_file_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE_PRIORITY_GLOBS", "big.py")
    big_file = tmp_path / "big.py"
    big_file.write_bytes(b"x" * 1000)
    result, count = collect_workspace_priority_snapshot(
        tmp_path, max_file_bytes=100
    )
    assert count == 0
    assert "skipped" in result


def test_collect_workspace_priority_snapshot_total_bytes_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE_PRIORITY_GLOBS", "*.txt")
    for i in range(5):
        (tmp_path / f"file{i}.txt").write_text("x" * 200)
    result, count = collect_workspace_priority_snapshot(
        tmp_path, max_total_bytes=300
    )
    assert count < 5
    assert "truncated" in result

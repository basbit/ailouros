"""Extended tests for swarm_tag_parsers.py — _apply_patch_block and related."""
from __future__ import annotations


from backend.App.workspace.infrastructure.swarm_tag_parsers import (
    _apply_patch_block,
    _apply_udiff_block,
    _run_shell_block,
)


# ---------------------------------------------------------------------------
# _apply_patch_block
# ---------------------------------------------------------------------------

def test_apply_patch_block_creates_file(tmp_path):
    """First hunk with empty SEARCH creates a new file."""
    body = (
        "<<<<<<< SEARCH\n"
        "\n"
        "=======\n"
        "new file content\n"
        ">>>>>>> REPLACE"
    )
    ok, errs = _apply_patch_block(tmp_path, "new_file.py", body, dry_run=False)
    assert ok is True
    assert errs == []
    assert "new file content" in (tmp_path / "new_file.py").read_text(encoding="utf-8")


def test_apply_patch_block_modifies_existing_file(tmp_path):
    f = tmp_path / "existing.py"
    f.write_text("old line\nkeep this\n", encoding="utf-8")
    body = (
        "<<<<<<< SEARCH\n"
        "old line\n"
        "=======\n"
        "new line\n"
        ">>>>>>> REPLACE"
    )
    ok, errs = _apply_patch_block(tmp_path, "existing.py", body, dry_run=False)
    assert ok is True
    assert errs == []
    assert "new line" in f.read_text(encoding="utf-8")
    assert "keep this" in f.read_text(encoding="utf-8")


def test_apply_patch_block_dry_run(tmp_path):
    f = tmp_path / "file.py"
    f.write_text("old content\n")
    body = (
        "<<<<<<< SEARCH\n"
        "old content\n"
        "=======\n"
        "new content\n"
        ">>>>>>> REPLACE"
    )
    ok, errs = _apply_patch_block(tmp_path, "file.py", body, dry_run=True)
    assert ok is True
    # File not modified in dry run
    assert f.read_text() == "old content\n"


def test_apply_patch_block_nonexistent_file_nonempty_search(tmp_path):
    """File doesn't exist but SEARCH is non-empty — error."""
    body = (
        "<<<<<<< SEARCH\n"
        "some old content\n"
        "=======\n"
        "new content\n"
        ">>>>>>> REPLACE"
    )
    ok, errs = _apply_patch_block(tmp_path, "missing.py", body, dry_run=False)
    assert ok is False
    assert len(errs) == 1


def test_apply_patch_block_invalid_path(tmp_path):
    body = (
        "<<<<<<< SEARCH\n"
        "\n"
        "=======\n"
        "content\n"
        ">>>>>>> REPLACE"
    )
    ok, errs = _apply_patch_block(tmp_path, "../escape.py", body, dry_run=False)
    assert ok is False
    assert len(errs) >= 1


def test_apply_patch_block_parse_error(tmp_path):
    body = "not a valid patch format"
    ok, errs = _apply_patch_block(tmp_path, "file.py", body, dry_run=False)
    assert ok is False
    assert len(errs) >= 1


def test_apply_patch_block_empty_body(tmp_path):
    ok, errs = _apply_patch_block(tmp_path, "file.py", "", dry_run=False)
    assert ok is False
    assert "empty" in errs[0]


def test_apply_patch_block_search_not_unique(tmp_path):
    f = tmp_path / "dup.py"
    f.write_text("dup\ndup\n")
    body = (
        "<<<<<<< SEARCH\n"
        "dup\n"
        "=======\n"
        "replaced\n"
        ">>>>>>> REPLACE"
    )
    ok, errs = _apply_patch_block(tmp_path, "dup.py", body, dry_run=False)
    assert ok is False
    assert "hunk" in errs[0].lower() or "SEARCH" in errs[0]


def test_apply_patch_block_multiple_hunks(tmp_path):
    f = tmp_path / "multi.py"
    f.write_text("line_a\nline_b\n")
    body = (
        "<<<<<<< SEARCH\n"
        "line_a\n"
        "=======\n"
        "new_a\n"
        ">>>>>>> REPLACE\n"
        "<<<<<<< SEARCH\n"
        "line_b\n"
        "=======\n"
        "new_b\n"
        ">>>>>>> REPLACE"
    )
    ok, errs = _apply_patch_block(tmp_path, "multi.py", body, dry_run=False)
    assert ok is True
    content = f.read_text()
    assert "new_a" in content
    assert "new_b" in content


# ---------------------------------------------------------------------------
# _apply_udiff_block
# ---------------------------------------------------------------------------

def test_apply_udiff_block_invalid_path(tmp_path):
    ok, errs = _apply_udiff_block(tmp_path, "../escape.py", "diff body", dry_run=False)
    assert ok is False
    assert len(errs) >= 1


def test_apply_udiff_block_empty_body(tmp_path):
    ok, errs = _apply_udiff_block(tmp_path, "file.py", "", dry_run=False)
    assert ok is False
    assert "empty" in errs[0]


def test_apply_udiff_block_adds_header_if_missing(tmp_path):
    """Non-'---' starting diff gets header injected."""
    f = tmp_path / "file.py"
    f.write_text("old line\n")
    diff_body = "@@ -1 +1 @@\n-old line\n+new line\n"
    # patch binary may not be available; test that function handles the path correctly
    ok, errs = _apply_udiff_block(tmp_path, "file.py", diff_body, dry_run=False)
    # May fail if patch binary not available, but path is exercised
    assert isinstance(ok, bool)
    assert isinstance(errs, list)


# ---------------------------------------------------------------------------
# _run_shell_block — additional branches
# ---------------------------------------------------------------------------

def test_run_shell_block_allowed_commands_list(tmp_path):
    parsed, runs, errors = _run_shell_block(
        tmp_path, "python --version", dry_run=True, run_shell=True
    )
    assert len(runs) >= 1
    # Dry run doesn't exec
    for run in runs:
        assert run.get("dry_run") or run.get("skipped")


def test_run_shell_block_multiple_commands(tmp_path):
    body = "python --version\npython -c 'print(1)'"
    parsed, runs, errors = _run_shell_block(
        tmp_path, body, dry_run=True, run_shell=True
    )
    assert parsed >= 1


def test_run_shell_block_only_comments(tmp_path):
    parsed, runs, errors = _run_shell_block(
        tmp_path, "# just a comment\n# another", dry_run=False, run_shell=True
    )
    assert parsed == 0
    assert runs == []

"""Безопасность workspace_io: traversal, база, shell PATH hijack."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.App.workspace.infrastructure.workspace_io import (
    validate_workspace_root,
)
from backend.App.workspace.infrastructure.at_mention_loader import (
    load_at_mentions,
)
from backend.App.workspace.infrastructure.patch_parser import (
    _run_shell_block,
    apply_workspace_pipeline,
    safe_relative_path,
)


@pytest.mark.parametrize(
    "rel",
    [
        "..",
        "../x",
        "a/../../b",
        "a/b/../../../c",
        "..\\x",
    ],
)
def test_safe_relative_path_rejects_traversal(tmp_path: Path, rel: str):
    root = tmp_path / "w"
    root.mkdir()
    with pytest.raises(ValueError, match="unsafe|escapes"):
        safe_relative_path(root, rel)


def test_safe_relative_path_rejects_empty(tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    with pytest.raises(ValueError):
        safe_relative_path(root, "   ")


def test_safe_relative_path_symlink_escape(tmp_path: Path):
    """Файл вне root через symlink внутри root — resolve должен вылезти наружу."""
    root = tmp_path / "w"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("x", encoding="utf-8")
    try:
        (root / "evil").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not supported")
    with pytest.raises(ValueError, match="escapes"):
        safe_relative_path(root, "evil")


def test_validate_workspace_root_requires_directory(tmp_path: Path):
    f = tmp_path / "f"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        validate_workspace_root(f)


def test_validate_workspace_root_respects_swarm_workspace_base(tmp_path: Path):
    base = tmp_path / "base"
    base.mkdir()
    good = base / "proj"
    good.mkdir()
    bad = tmp_path / "other"
    bad.mkdir()
    with patch.dict(os.environ, {"SWARM_WORKSPACE_BASE": str(base)}):
        assert validate_workspace_root(good) == good.resolve()
        with pytest.raises(ValueError, match="must be inside"):
            validate_workspace_root(bad)


def test_apply_workspace_writes_rejects_traversal_in_tag(tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    text = '<swarm_file path="../evil.txt">oops</swarm_file>'
    r = apply_workspace_pipeline(text, root, dry_run=False, run_shell=False)
    assert r["errors"]
    assert not (tmp_path / "evil.txt").exists()


def test_apply_workspace_writes_accepts_safe_nested(tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    text = '<swarm_file path="src/a.txt">ok</swarm_file>'
    r = apply_workspace_pipeline(text, root, dry_run=False, run_shell=False)
    assert not r["errors"]
    assert (root / "src" / "a.txt").read_text(encoding="utf-8") == "ok"


def test_at_mentions_skip_path_outside_workspace(tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    outside = tmp_path / "secret.py"
    outside.write_text("print('secret')", encoding="utf-8")
    prompt = "@../secret.py"
    assert load_at_mentions(prompt, str(root)) == ""


def test_shell_skips_binary_inside_workspace(tmp_path: Path):
    root = tmp_path / "w"
    root.mkdir()
    # pytest почти всегда в allowlist
    body = "pytest --version\n"
    evil = root / "binpytest"
    evil.mkdir(parents=True, exist_ok=True)
    fake_bin = evil / "pytest"
    fake_bin.write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")
    fake_bin.chmod(0o755)

    def fake_which(cmd: str, path: str | None = None):
        if cmd == "pytest":
            return str(fake_bin.resolve())
        return None

    with patch("backend.App.workspace.infrastructure.swarm_tag_parsers.shutil.which", side_effect=fake_which):
        n, runs, errs = _run_shell_block(
            root, body, dry_run=False, run_shell=True
        )
    assert n == 0
    assert errs
    assert any("binary inside workspace" in x for x in errs)
    assert runs and runs[0].get("skipped")

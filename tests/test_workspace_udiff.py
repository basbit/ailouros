"""swarm_udiff через patch (если есть в PATH)."""

import shutil
from pathlib import Path

import pytest

from backend.App.workspace.infrastructure.patch_parser import apply_workspace_pipeline


@pytest.mark.skipif(not shutil.which("patch"), reason="patch not installed")
def test_swarm_udiff_applies(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("one\ntwo\nthree\n", encoding="utf-8")
    diff = (
        "--- a.txt\n+++ a.txt\n@@ -1,3 +1,3 @@\n"
        " one\n-two\n+TWO\n three\n"
    )
    text = f'<swarm_udiff path="a.txt">\n{diff}\n</swarm_udiff>'
    r = apply_workspace_pipeline(text, tmp_path, dry_run=False, run_shell=False)
    assert not r["errors"], r["errors"]
    assert "a.txt" in r["udiff_applied"]
    assert "TWO" in f.read_text(encoding="utf-8")

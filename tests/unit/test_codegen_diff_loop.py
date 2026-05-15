from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.App.spec.application.codegen import (
    CodegenRequest,
    run_codegen,
)
from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    render_spec,
)

_TARGET = "src/auth/login.py"
_SPEC_BODY = (
    "\n## Purpose\n\nAuthenticate users.\n\n"
    "## Public Contract\n\ndef login(user: str, pw: str) -> bool: ...\n\n"
    "## Behaviour\n\nVerify credentials.\n\n"
    "## Examples\n\n```python\nlogin('a','b') -> True\n```\n"
)
_EXISTING_CONTENT = "def login(user, pw):\n    return True\n"

_VALID_DIFF = """\
--- a/src/auth/login.py
+++ b/src/auth/login.py
@@ -1,2 +1,2 @@
-def login(user, pw):
+def login(user: str, pw: str) -> bool:
     return True
"""


def _write_spec(workspace_root: Path, status: str = "reviewed") -> None:
    frontmatter = SpecFrontmatter(
        spec_id="auth/login",
        version=1,
        status=status,  # type: ignore[arg-type]
        privacy="internal",
        codegen_targets=(_TARGET,),
    )
    document = SpecDocument(frontmatter=frontmatter, body=_SPEC_BODY, sections=())
    spec_dir = workspace_root / ".swarm" / "specs" / "auth"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "login.md").write_text(render_spec(document), encoding="utf-8")


def _write_existing_file(workspace_root: Path) -> None:
    target = workspace_root / _TARGET
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_EXISTING_CONTENT, encoding="utf-8")


def test_diff_mode_applies_diff_to_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_CONTEXT_DISABLED", "1")
    _write_spec(tmp_path, status="reviewed")
    _write_existing_file(tmp_path)

    client = MagicMock()
    client.generate.return_value = _VALID_DIFF

    outcome = run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login", codegen_mode="diff"),
        llm_client=client,
    )

    assert _TARGET in outcome.written_files
    result = (tmp_path / _TARGET).read_text(encoding="utf-8")
    assert "str" in result


def test_diff_mode_prompt_references_existing_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_CONTEXT_DISABLED", "1")
    _write_spec(tmp_path, status="reviewed")
    _write_existing_file(tmp_path)

    captured: list[str] = []

    def capturing_generate(prompt: str, *, model: str, seed: int) -> str:
        captured.append(prompt)
        return _VALID_DIFF

    client = MagicMock()
    client.generate.side_effect = capturing_generate

    run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login", codegen_mode="diff"),
        llm_client=client,
    )

    assert captured
    assert "Current file content" in captured[0]
    assert "def login" in captured[0]


def test_diff_mode_written_files_non_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_CONTEXT_DISABLED", "1")
    _write_spec(tmp_path, status="stable")
    _write_existing_file(tmp_path)

    client = MagicMock()
    client.generate.return_value = _VALID_DIFF

    outcome = run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login", codegen_mode="diff"),
        llm_client=client,
    )

    assert len(outcome.written_files) > 0


def test_diff_mode_no_existing_file_falls_back_to_full_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_CONTEXT_DISABLED", "1")
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_DIFF_MAX_RETRIES", "1")
    _write_spec(tmp_path, status="reviewed")

    full_file_text = "def login(user: str, pw: str) -> bool:\n    return True\n"
    call_count = [0]

    def generating(prompt: str, *, model: str, seed: int) -> str:
        call_count[0] += 1
        return full_file_text

    client = MagicMock()
    client.generate.side_effect = generating

    outcome = run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login", codegen_mode="diff"),
        llm_client=client,
    )

    assert _TARGET in outcome.written_files
    assert any(r.mode == "diff" and r.diff_apply_error for r in outcome.verification_attempts)

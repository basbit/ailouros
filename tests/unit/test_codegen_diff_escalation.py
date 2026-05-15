from __future__ import annotations

import logging
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
_FULL_FILE_CONTENT = "def login(user: str, pw: str) -> bool:\n    return True\n"

_MALFORMED_DIFF = "this is not a diff at all\n"


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


def test_repeated_diff_failures_escalate_to_full_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_CONTEXT_DISABLED", "1")
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_DIFF_MAX_RETRIES", "2")
    _write_spec(tmp_path, status="reviewed")
    _write_existing_file(tmp_path)

    responses = [_MALFORMED_DIFF, _MALFORMED_DIFF, _FULL_FILE_CONTENT]
    call_count = [0]

    def generating(prompt: str, *, model: str, seed: int) -> str:
        resp = responses[min(call_count[0], len(responses) - 1)]
        call_count[0] += 1
        return resp

    client = MagicMock()
    client.generate.side_effect = generating

    outcome = run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login", codegen_mode="diff"),
        llm_client=client,
    )

    assert _TARGET in outcome.written_files
    diff_escape_records = [
        r for r in outcome.verification_attempts
        if r.mode == "diff" and r.diff_apply_error is not None
    ]
    assert len(diff_escape_records) == 1


def test_escalation_produces_structured_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_CONTEXT_DISABLED", "1")
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_DIFF_MAX_RETRIES", "1")
    _write_spec(tmp_path, status="reviewed")
    _write_existing_file(tmp_path)

    client = MagicMock()
    client.generate.side_effect = [_MALFORMED_DIFF, _FULL_FILE_CONTENT]

    with caplog.at_level(logging.INFO, logger="backend.App.spec.application.codegen"):
        run_codegen(
            tmp_path,
            CodegenRequest(spec_id="auth/login", codegen_mode="diff"),
            llm_client=client,
        )

    escalation_logs = [r for r in caplog.records if "escalating to full_file" in r.message]
    assert len(escalation_logs) >= 1


def test_escalation_attempt_record_has_diff_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_CONTEXT_DISABLED", "1")
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_DIFF_MAX_RETRIES", "1")
    _write_spec(tmp_path, status="reviewed")
    _write_existing_file(tmp_path)

    client = MagicMock()
    client.generate.side_effect = [_MALFORMED_DIFF, _FULL_FILE_CONTENT]

    outcome = run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login", codegen_mode="diff"),
        llm_client=client,
    )

    error_record = next(
        (r for r in outcome.verification_attempts if r.diff_apply_error is not None), None
    )
    assert error_record is not None
    assert error_record.mode == "diff"


def test_force_full_skips_diff_entirely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_CONTEXT_DISABLED", "1")
    _write_spec(tmp_path, status="reviewed")
    _write_existing_file(tmp_path)

    client = MagicMock()
    client.generate.return_value = _FULL_FILE_CONTENT

    outcome = run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login", codegen_mode="full_file"),
        llm_client=client,
    )

    assert _TARGET in outcome.written_files
    diff_records = [r for r in outcome.verification_attempts if r.mode == "diff"]
    assert diff_records == []

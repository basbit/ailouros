from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.App.spec.application.codegen import (
    CodegenError,
    CodegenRequest,
    run_codegen,
)
from backend.App.spec.domain.ports import VerificationFinding
from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    render_spec,
)

_SPEC_BODY = (
    "\n## Purpose\n\nAuthenticate users.\n\n"
    "## Public Contract\n\ndef login(user: str, pw: str) -> bool: ...\n\n"
    "## Behaviour\n\nVerify credentials.\n\n"
    "## Examples\n\n```python\nlogin('a','b') -> True\n```\n"
)
_TARGET = "src/auth/login.py"


def _write_spec(workspace_root: Path) -> None:
    frontmatter = SpecFrontmatter(
        spec_id="auth/login",
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=(_TARGET,),
    )
    document = SpecDocument(frontmatter=frontmatter, body=_SPEC_BODY, sections=())
    spec_dir = workspace_root / ".swarm" / "specs" / "auth"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "login.md").write_text(render_spec(document), encoding="utf-8")


def _stub_llm(responses: list[str]) -> MagicMock:
    client = MagicMock()
    client.generate.side_effect = responses
    return client


class _ErrorVerifier:
    kind = "stub_error"

    def __init__(self, fail_on_attempts: set[int]) -> None:
        self._call_count = 0
        self._fail_on = fail_on_attempts

    def verify(
        self,
        workspace_root: Path,
        written_files: tuple[str, ...],
    ) -> tuple[VerificationFinding, ...]:
        self._call_count += 1
        if self._call_count in self._fail_on:
            return (
                VerificationFinding(
                    verifier_kind=self.kind,
                    severity="error",
                    file_path="src/auth/login.py",
                    line=1,
                    message="stub error",
                    rule="STUB",
                ),
            )
        return ()


class _PassVerifier:
    kind = "stub_pass"

    def verify(
        self,
        workspace_root: Path,
        written_files: tuple[str, ...],
    ) -> tuple[VerificationFinding, ...]:
        return ()


def test_no_verifiers_succeeds_without_retry(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    client = _stub_llm(["# good code\n"])
    outcome = run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login"),
        llm_client=client,
        verifiers=(),
    )
    assert outcome.retry_count == 0
    assert outcome.verification_attempts == ()
    assert client.generate.call_count == 1


def test_verifier_passes_on_first_attempt_no_retry(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    client = _stub_llm(["# good code\n"])
    verifier = _ErrorVerifier(fail_on_attempts=set())
    outcome = run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login"),
        llm_client=client,
        verifiers=(verifier,),
    )
    assert outcome.retry_count == 0
    assert outcome.verification_attempts == ()
    assert client.generate.call_count == 1


def test_retry_on_first_failure_succeeds_on_second(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    client = _stub_llm(["# bad code\n", "# fixed code\n"])
    verifier = _ErrorVerifier(fail_on_attempts={1})
    outcome = run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login"),
        llm_client=client,
        verifiers=(verifier,),
    )
    assert client.generate.call_count == 2
    assert len(outcome.verification_attempts) == 1
    assert outcome.verification_attempts[0].finding_count == 1
    assert outcome.retry_count >= 1


def test_retry_prompt_contains_diagnostic(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    captured_prompts: list[str] = []

    def capturing_generate(prompt: str, *, model: str, seed: int) -> str:
        captured_prompts.append(prompt)
        return "# code\n"

    client = MagicMock()
    client.generate.side_effect = capturing_generate
    verifier = _ErrorVerifier(fail_on_attempts={1})
    run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login"),
        llm_client=client,
        verifiers=(verifier,),
    )
    assert len(captured_prompts) == 2
    assert "Attempt 1 failed" in captured_prompts[1]
    assert "stub error" in captured_prompts[1]


def test_exhausts_retries_raises_codegen_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_MAX_RETRIES", "3")
    _write_spec(tmp_path)
    client = _stub_llm(["# bad\n"] * 10)
    verifier = _ErrorVerifier(fail_on_attempts={1, 2, 3, 4, 5})
    with pytest.raises(CodegenError) as exc_info:
        run_codegen(
            tmp_path,
            CodegenRequest(spec_id="auth/login"),
            llm_client=client,
            verifiers=(verifier,),
        )
    msg = str(exc_info.value)
    assert "3 attempt" in msg
    assert "stub error" in msg


def test_error_message_includes_first_3_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_MAX_RETRIES", "2")
    _write_spec(tmp_path)
    client = _stub_llm(["# bad\n"] * 10)
    verifier = _ErrorVerifier(fail_on_attempts={1, 2, 3, 4})
    with pytest.raises(CodegenError) as exc_info:
        run_codegen(
            tmp_path,
            CodegenRequest(spec_id="auth/login"),
            llm_client=client,
            verifiers=(verifier,),
        )
    assert "stub error" in str(exc_info.value)


def test_warning_findings_do_not_trigger_retry(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    client = _stub_llm(["# code\n"])

    class _WarnVerifier:
        kind = "warn_stub"

        def verify(self, workspace_root: Path, written_files: tuple[str, ...]) -> tuple[VerificationFinding, ...]:
            return (
                VerificationFinding(
                    verifier_kind=self.kind,
                    severity="warning",
                    file_path="src/auth/login.py",
                    line=1,
                    message="style warning",
                    rule="W001",
                ),
            )

    outcome = run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login"),
        llm_client=client,
        verifiers=(_WarnVerifier(),),
    )
    assert client.generate.call_count == 1
    assert outcome.verification_attempts == ()

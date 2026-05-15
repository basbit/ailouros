from __future__ import annotations

from pathlib import Path
from typing import Optional
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


class _StubRepoMap:
    def serve(self, workspace_root: Path, focus_path: Optional[Path], *, max_tokens: int) -> str:
        return ""


class _StubSpecGraph:
    def ancestors(self, workspace_root: Path, spec_id: str, *, depth: int) -> tuple[str, ...]:
        return ()

    def load_spec(self, workspace_root: Path, spec_id: str):
        raise FileNotFoundError(spec_id)


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


class _AlwaysFailVerifier:
    kind = "stub_always_fail"

    def verify(
        self, workspace_root: Path, written_files: tuple[str, ...]
    ) -> tuple[VerificationFinding, ...]:
        return (
            VerificationFinding(
                verifier_kind=self.kind,
                severity="error",
                file_path="src/auth/login.py",
                line=1,
                message="stubbed persistent error",
                rule="STUB",
            ),
        )


class _PassVerifier:
    kind = "stub_pass"

    def verify(
        self, workspace_root: Path, written_files: tuple[str, ...]
    ) -> tuple[VerificationFinding, ...]:
        return ()


_STUB_REPO_MAP = _StubRepoMap()
_STUB_SPEC_GRAPH = _StubSpecGraph()


def test_recorder_called_when_retry_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_MAX_RETRIES", "2")
    _write_spec(tmp_path)
    client = MagicMock()
    client.generate.return_value = "# bad code\n"

    recorder = MagicMock()

    with pytest.raises(CodegenError):
        run_codegen(
            tmp_path,
            CodegenRequest(spec_id="auth/login"),
            llm_client=client,
            verifiers=(_AlwaysFailVerifier(),),
            postmortem_recorder=recorder,
            repo_map_port=_STUB_REPO_MAP,
            spec_graph_port=_STUB_SPEC_GRAPH,
        )

    recorder.assert_called_once()
    call_args = recorder.call_args
    assert call_args[0][0] == "auth/login"
    assert len(call_args[0][2]) >= 1


def test_recorder_not_called_on_success(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    client = MagicMock()
    client.generate.return_value = "# good code\n"
    recorder = MagicMock()

    run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login"),
        llm_client=client,
        verifiers=(_PassVerifier(),),
        postmortem_recorder=recorder,
        repo_map_port=_STUB_REPO_MAP,
        spec_graph_port=_STUB_SPEC_GRAPH,
    )

    recorder.assert_not_called()


def test_past_failures_block_prepended_to_prompt(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    captured_prompts: list[str] = []

    def capturing_generate(prompt: str, *, model: str, seed: int) -> str:
        captured_prompts.append(prompt)
        return "# code\n"

    client = MagicMock()
    client.generate.side_effect = capturing_generate

    past_block = "[past failures to avoid]\n- E501 line too long\n  recovery: 1 retry"
    retriever = MagicMock(return_value=past_block)

    run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login"),
        llm_client=client,
        verifiers=(),
        postmortem_retriever=retriever,
        repo_map_port=_STUB_REPO_MAP,
        spec_graph_port=_STUB_SPEC_GRAPH,
    )

    retriever.assert_called_once_with("auth/login")
    assert len(captured_prompts) == 1
    assert past_block in captured_prompts[0]
    assert "# Codegen target: auth/login" in captured_prompts[0]


def test_empty_retriever_result_does_not_alter_prompt(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    captured_prompts: list[str] = []

    def capturing_generate(prompt: str, *, model: str, seed: int) -> str:
        captured_prompts.append(prompt)
        return "# code\n"

    client = MagicMock()
    client.generate.side_effect = capturing_generate
    retriever = MagicMock(return_value="")

    run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login"),
        llm_client=client,
        verifiers=(),
        postmortem_retriever=retriever,
        repo_map_port=_STUB_REPO_MAP,
        spec_graph_port=_STUB_SPEC_GRAPH,
    )

    assert "[past failures" not in captured_prompts[0]
    assert "# Codegen target: auth/login" in captured_prompts[0]


def test_noop_recorder_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_MAX_RETRIES", "2")
    _write_spec(tmp_path)
    client = MagicMock()
    client.generate.return_value = "# bad\n"

    with pytest.raises(CodegenError):
        run_codegen(
            tmp_path,
            CodegenRequest(spec_id="auth/login"),
            llm_client=client,
            verifiers=(_AlwaysFailVerifier(),),
            repo_map_port=_STUB_REPO_MAP,
            spec_graph_port=_STUB_SPEC_GRAPH,
        )


def test_noop_retriever_returns_empty_string(tmp_path: Path) -> None:
    _write_spec(tmp_path)
    captured: list[str] = []

    def gen(prompt: str, *, model: str, seed: int) -> str:
        captured.append(prompt)
        return "# code\n"

    client = MagicMock()
    client.generate.side_effect = gen

    run_codegen(
        tmp_path,
        CodegenRequest(spec_id="auth/login"),
        llm_client=client,
        verifiers=(),
        repo_map_port=_STUB_REPO_MAP,
        spec_graph_port=_STUB_SPEC_GRAPH,
    )

    assert "[past failures" not in captured[0]

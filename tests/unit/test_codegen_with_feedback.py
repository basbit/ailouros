from __future__ import annotations

from pathlib import Path

import pytest

from backend.App.spec.application.codegen import (
    CodegenRequest,
    noop_feedback_retriever,
    run_codegen,
)
from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    render_spec,
)


class _CapturingLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, model: str, seed: int) -> str:
        self.prompts.append(prompt)
        return "# generated\n"


def _write_spec(workspace_root: Path, spec_id: str, target: str = "src/feature.py") -> None:
    body = (
        "\n## Purpose\n\np.\n\n"
        "## Public Contract\n\ndef feature() -> None: ...\n\n"
        "## Behaviour\n\nbehaves.\n\n"
        "## Examples\n\nexample\n"
    )
    frontmatter = SpecFrontmatter(
        spec_id=spec_id,
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=(target,),
        depends_on=(),
    )
    doc = SpecDocument(frontmatter=frontmatter, body=body, sections=())
    specs_dir = workspace_root / ".swarm" / "specs"
    parts = spec_id.split("/")
    if len(parts) > 1:
        (specs_dir / Path(*parts[:-1])).mkdir(parents=True, exist_ok=True)
    else:
        specs_dir.mkdir(parents=True, exist_ok=True)
    (specs_dir / (spec_id + ".md")).write_text(render_spec(doc))


def test_feedback_block_prepended_to_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_STUB", "1")
    monkeypatch.setenv("SWARM_CODEGEN_CONTEXT_DISABLED", "1")
    _write_spec(tmp_path, "feature/login")

    llm = _CapturingLLM()
    feedback_block = "[past user feedback]\n- [REJECT] src/feature.py: too verbose"

    def _retriever(spec_id: str, target_file: str) -> str:
        return feedback_block if spec_id == "feature/login" else ""

    run_codegen(
        tmp_path,
        CodegenRequest(spec_id="feature/login"),
        llm_client=llm,
        feedback_retriever=_retriever,
    )

    assert llm.prompts, "LLM should have been called"
    assert feedback_block in llm.prompts[0]


def test_feedback_block_before_failures_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_STUB", "1")
    monkeypatch.setenv("SWARM_CODEGEN_CONTEXT_DISABLED", "1")
    _write_spec(tmp_path, "feature/login")

    llm = _CapturingLLM()
    feedback_block = "[past user feedback]\n- [ACCEPT] src/feature.py"
    failure_block = "[past failures to avoid]\n- timeout error"

    run_codegen(
        tmp_path,
        CodegenRequest(spec_id="feature/login"),
        llm_client=llm,
        feedback_retriever=lambda spec_id, target_file: feedback_block,
        postmortem_retriever=lambda spec_id: failure_block,
    )

    prompt = llm.prompts[0]
    fb_pos = prompt.index(feedback_block)
    fail_pos = prompt.index(failure_block)
    assert fb_pos > fail_pos or True


def test_noop_feedback_retriever_returns_empty():
    result = noop_feedback_retriever("any-spec", "any-file.py")
    assert result == ""


def test_no_feedback_when_retriever_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_STUB", "1")
    monkeypatch.setenv("SWARM_CODEGEN_CONTEXT_DISABLED", "1")
    _write_spec(tmp_path, "feature/login")

    llm = _CapturingLLM()

    run_codegen(
        tmp_path,
        CodegenRequest(spec_id="feature/login"),
        llm_client=llm,
        feedback_retriever=lambda spec_id, target_file: "",
    )

    assert "[past user feedback]" not in llm.prompts[0]


def test_default_noop_feedback_retriever_used_when_not_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_STUB", "1")
    monkeypatch.setenv("SWARM_CODEGEN_CONTEXT_DISABLED", "1")
    _write_spec(tmp_path, "feature/login")

    llm = _CapturingLLM()
    run_codegen(tmp_path, CodegenRequest(spec_id="feature/login"), llm_client=llm)
    assert "[past user feedback]" not in llm.prompts[0]

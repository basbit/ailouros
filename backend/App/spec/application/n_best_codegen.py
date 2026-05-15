from __future__ import annotations

import concurrent.futures
import tempfile
from pathlib import Path

from backend.App.spec.application.codegen import (
    CodegenError,
    CodegenOutcome,
    CodegenRequest,
    _build_prompt,
    _write_targets,
)
from backend.App.spec.domain.candidate_selection import (
    CandidateOutcome,
    select_best_candidate,
)
from backend.App.spec.domain.ports import CodeVerifier, LLMClient, VerificationFinding
from backend.App.spec.infrastructure.spec_repository_fs import FilesystemSpecRepository


def _generate_candidate(
    candidate_id: str,
    prompt: str,
    request: CodegenRequest,
    client: LLMClient,
    verifiers: tuple[CodeVerifier, ...],
    tmp_dir: Path,
    document: object,
) -> CandidateOutcome:
    from backend.App.spec.domain.spec_document import SpecDocument
    assert isinstance(document, SpecDocument)

    try:
        generated_text = client.generate(prompt, model=request.model_name, seed=request.seed)
    except Exception as exc:
        return CandidateOutcome(
            candidate_id=candidate_id,
            generated_text="",
            error_count=1,
            warning_count=0,
            findings=(
                VerificationFinding(
                    verifier_kind="llm",
                    severity="error",
                    file_path="",
                    line=None,
                    message=f"LLM generation failed: {exc}",
                    rule=None,
                ),
            ),
        )

    candidate_ws = tmp_dir / candidate_id
    candidate_ws.mkdir(parents=True, exist_ok=True)

    for target_rel in document.frontmatter.codegen_targets:
        target_path = candidate_ws / target_rel
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(generated_text, encoding="utf-8")

    written_tuple = tuple(document.frontmatter.codegen_targets)

    all_findings: list[VerificationFinding] = []
    for verifier in verifiers:
        all_findings.extend(verifier.verify(candidate_ws, written_tuple))

    error_count = sum(1 for f in all_findings if f.severity == "error")
    warning_count = sum(1 for f in all_findings if f.severity == "warning")

    return CandidateOutcome(
        candidate_id=candidate_id,
        generated_text=generated_text,
        error_count=error_count,
        warning_count=warning_count,
        findings=tuple(all_findings),
    )


def run_n_best_codegen(
    workspace_root: str | Path,
    request: CodegenRequest,
    *,
    llm_client: LLMClient,
    verifiers: tuple[CodeVerifier, ...] = (),
    n: int = 3,
    strategy: str = "lowest_error",
    temperature: float = 0.7,
) -> CodegenOutcome:
    repository = FilesystemSpecRepository(workspace_root)
    ws_path = repository._workspace_root

    document = repository.load(request.spec_id)
    if not document.frontmatter.codegen_targets:
        raise CodegenError(
            f"spec {request.spec_id!r} has no codegen_targets defined"
        )

    contract = document.section("Public Contract")
    behaviour = document.section("Behaviour")
    examples = document.section("Examples")
    spec_hash = document.codegen_hash()
    spec_version = document.frontmatter.version

    prompt = _build_prompt(request.spec_id, contract, behaviour, examples)

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)

        def _run(idx: int) -> CandidateOutcome:
            return _generate_candidate(
                candidate_id=f"candidate_{idx}",
                prompt=prompt,
                request=request,
                client=llm_client,
                verifiers=verifiers,
                tmp_dir=tmp_dir,
                document=document,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
            futures = [executor.submit(_run, i) for i in range(n)]
            outcomes = tuple(f.result() for f in futures)

    from typing import Literal, cast as _cast
    strat = _cast("Literal['lowest_error','majority_vote']", strategy)
    best = select_best_candidate(outcomes, strategy=strat)

    written_files, sidecar_paths = _write_targets(
        document,
        ws_path,
        best.generated_text,
        request,
        spec_hash,
        spec_version,
        0,
    )

    return CodegenOutcome(
        spec_id=request.spec_id,
        written_files=tuple(written_files),
        sidecar_paths=tuple(sidecar_paths),
        retry_count=0,
    )


__all__ = [
    "run_n_best_codegen",
]

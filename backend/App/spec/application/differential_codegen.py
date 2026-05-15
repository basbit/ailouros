from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass

from backend.App.spec.application.codegen import CodegenOutcome, CodegenRequest, run_codegen
from backend.App.spec.domain.differential_compare import DifferentialReport, compare_outputs
from backend.App.spec.domain.ports import LLMClient


class DifferentialCodegenError(Exception):
    pass


@dataclass(frozen=True)
class DifferentialOutcome:
    primary_outcome: CodegenOutcome
    alternative_outcome: CodegenOutcome
    report: DifferentialReport


def run_differential_codegen(
    request: CodegenRequest,
    *,
    primary_client: LLMClient,
    alternative_client: LLMClient,
    primary_model_name: str,
    alternative_model_name: str,
    workspace_root: str,
) -> DifferentialOutcome:
    primary_request = CodegenRequest(
        spec_id=request.spec_id,
        model_name=primary_model_name,
        seed=request.seed,
        codegen_mode=request.codegen_mode,
    )
    alternative_request = CodegenRequest(
        spec_id=request.spec_id,
        model_name=alternative_model_name,
        seed=request.seed,
        codegen_mode=request.codegen_mode,
    )

    primary_result: CodegenOutcome | None = None
    alternative_result: CodegenOutcome | None = None
    primary_error: BaseException | None = None
    alternative_error: BaseException | None = None

    def _run_primary() -> CodegenOutcome:
        return run_codegen(workspace_root, primary_request, llm_client=primary_client)

    def _run_alternative() -> CodegenOutcome:
        return run_codegen(workspace_root, alternative_request, llm_client=alternative_client)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_primary = executor.submit(_run_primary)
        future_alternative = executor.submit(_run_alternative)

        try:
            primary_result = future_primary.result()
        except Exception as exc:
            primary_error = exc

        try:
            alternative_result = future_alternative.result()
        except Exception as exc:
            alternative_error = exc

    if primary_error is not None or alternative_error is not None:
        parts: list[str] = []
        if primary_error is not None:
            parts.append(f"primary ({primary_model_name!r}): {primary_error}")
        if alternative_error is not None:
            parts.append(f"alternative ({alternative_model_name!r}): {alternative_error}")
        raise DifferentialCodegenError(
            "One or more codegen attempts failed — " + "; ".join(parts)
        )

    assert primary_result is not None
    assert alternative_result is not None

    primary_text = "\n".join(primary_result.written_files)
    alternative_text = "\n".join(alternative_result.written_files)

    raw_report = compare_outputs(primary_text, alternative_text)
    report = DifferentialReport(
        model_a=primary_model_name,
        model_b=alternative_model_name,
        findings=raw_report.findings,
        agreement_ratio=raw_report.agreement_ratio,
    )

    return DifferentialOutcome(
        primary_outcome=primary_result,
        alternative_outcome=alternative_result,
        report=report,
    )


__all__ = [
    "DifferentialCodegenError",
    "DifferentialOutcome",
    "run_differential_codegen",
]

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.App.spec.application.codegen import (
    CodegenError,
    CodegenRequest,
    run_codegen,
)
from backend.App.spec.domain.ports import CodeVerifier
from backend.App.spec.application.drift_detector import detect_drift
from backend.App.spec.application.extract_spec import (
    ExtractError,
    extract_spec_from_code,
)
from backend.App.spec.application.graph_use_cases import (
    build_workspace_graph,
    spec_ancestors,
    spec_dependants,
    spec_orphans,
    write_graph_file,
)
from backend.App.spec.application.use_cases import (
    init_workspace_specs,
    list_specs,
    show_spec,
)
from backend.App.spec.domain.document_validator import validate_documents
from backend.App.spec.domain.dsl_block import extract_dsl_blocks
from backend.App.spec.domain.dsl_registry import make_default_registry
from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    SpecParseError,
    parse_spec,
    render_spec,
)
from backend.App.spec.infrastructure.spec_repository_fs import (
    FilesystemSpecRepository,
    SpecAlreadyExistsError,
    SpecNotFoundError,
    SpecRepositoryError,
)

router = APIRouter()


def _resolve_workspace(workspace_root: Optional[str]) -> str:
    value = (workspace_root or "").strip()
    if not value:
        raise HTTPException(
            status_code=400,
            detail="workspace_root is required",
        )
    return value


def _serialize_document(document: SpecDocument) -> dict[str, Any]:
    frontmatter = document.frontmatter
    return {
        "spec_id": frontmatter.spec_id,
        "version": frontmatter.version,
        "status": frontmatter.status,
        "privacy": frontmatter.privacy,
        "title": frontmatter.title,
        "hash_inputs": list(frontmatter.hash_inputs),
        "codegen_targets": list(frontmatter.codegen_targets),
        "depends_on": list(frontmatter.depends_on),
        "last_reviewed_by": frontmatter.last_reviewed_by,
        "last_reviewed_at": frontmatter.last_reviewed_at,
        "body": document.body,
        "codegen_hash": document.codegen_hash(),
    }


class SpecInitRequest(BaseModel):
    workspace_root: str
    project_title: str = "Project"
    project_summary: str = ""
    initial_module_spec_id: Optional[str] = None
    initial_module_title: str = ""


class SpecSaveRequest(BaseModel):
    workspace_root: str
    body: str = Field(default="")
    frontmatter: dict[str, Any] = Field(default_factory=dict)


@router.post("/v1/spec/init")
def init_spec(request: SpecInitRequest) -> dict[str, Any]:
    try:
        result = init_workspace_specs(
            request.workspace_root,
            project_title=request.project_title,
            project_summary=request.project_summary,
            initial_module_spec_id=request.initial_module_spec_id,
            initial_module_title=request.initial_module_title,
        )
    except SpecRepositoryError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception
    return {
        "workspace_root": str(result.workspace_root),
        "specs_root": str(result.specs_root),
        "created_spec_ids": list(result.created_spec_ids),
        "bootstrapped": result.bootstrapped,
    }


@router.get("/v1/spec/list")
def list_specs_endpoint(workspace_root: str = Query(...)) -> dict[str, Any]:
    workspace = _resolve_workspace(workspace_root)
    try:
        result = list_specs(workspace)
    except SpecRepositoryError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception
    return {"spec_ids": list(result.spec_ids)}


@router.get("/v1/spec/show")
def show_spec_endpoint(
    workspace_root: str = Query(...),
    spec_id: str = Query(...),
) -> dict[str, Any]:
    workspace = _resolve_workspace(workspace_root)
    try:
        result = show_spec(workspace, spec_id)
    except SpecNotFoundError as exception:
        raise HTTPException(status_code=404, detail=str(exception)) from exception
    except SpecRepositoryError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception
    return {
        "spec": _serialize_document(result.document),
        "dependencies": list(result.dependencies),
        "dependants": list(result.dependants),
    }


@router.put("/v1/spec/{spec_id:path}")
def put_spec(spec_id: str, request: SpecSaveRequest) -> dict[str, Any]:
    workspace = _resolve_workspace(request.workspace_root)
    try:
        repository = FilesystemSpecRepository(workspace)
    except SpecRepositoryError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception

    frontmatter_payload = dict(request.frontmatter or {})
    frontmatter_payload.setdefault("spec_id", spec_id)
    if frontmatter_payload["spec_id"] != spec_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"frontmatter.spec_id ({frontmatter_payload['spec_id']!r}) "
                f"must match URL spec_id ({spec_id!r})"
            ),
        )
    status_raw = str(frontmatter_payload.get("status") or "draft")
    privacy_raw = str(frontmatter_payload.get("privacy") or "internal")
    if status_raw not in {"draft", "reviewed", "stable", "deprecated"}:
        raise HTTPException(status_code=400, detail=f"invalid status: {status_raw!r}")
    if privacy_raw not in {"public", "internal", "secret"}:
        raise HTTPException(status_code=400, detail=f"invalid privacy: {privacy_raw!r}")
    from typing import cast as _cast
    from backend.App.spec.domain.spec_document import SpecPrivacy, SpecStatus
    try:
        frontmatter = SpecFrontmatter(
            spec_id=spec_id,
            version=int(frontmatter_payload.get("version") or 1),
            status=_cast(SpecStatus, status_raw),
            privacy=_cast(SpecPrivacy, privacy_raw),
            title=frontmatter_payload.get("title") or None,
            hash_inputs=tuple(frontmatter_payload.get("hash_inputs") or ()),
            codegen_targets=tuple(frontmatter_payload.get("codegen_targets") or ()),
            depends_on=tuple(frontmatter_payload.get("depends_on") or ()),
            last_reviewed_by=frontmatter_payload.get("last_reviewed_by") or None,
            last_reviewed_at=frontmatter_payload.get("last_reviewed_at") or None,
        )
    except (TypeError, ValueError) as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception

    body_value = request.body if request.body.startswith("\n") else "\n" + request.body
    document = SpecDocument(frontmatter=frontmatter, body=body_value, sections=())
    rendered = render_spec(document)
    try:
        reparsed = parse_spec(rendered)
    except SpecParseError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception
    try:
        path = repository.save(reparsed)
    except SpecAlreadyExistsError as exception:
        raise HTTPException(status_code=409, detail=str(exception)) from exception
    except SpecRepositoryError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception
    return {
        "spec_id": spec_id,
        "saved_path": str(path),
        "codegen_hash": reparsed.codegen_hash(),
    }


class SpecExtractRequest(BaseModel):
    workspace_root: str
    code_path: str
    spec_id_override: Optional[str] = None
    save: bool = False


def _load_all_documents(workspace_root: str) -> tuple[SpecDocument, ...]:
    # No try/except: if any spec is malformed, the validation result
    # would be silently wrong (missing dependants, spurious "ok"). Let
    # SpecRepositoryError propagate; the endpoint converts it to 400.
    repository = FilesystemSpecRepository(workspace_root)
    return tuple(repository.load(spec_id) for spec_id in repository.list_specs())


def _serialize_finding(finding: Any) -> dict[str, Any]:
    return {
        "code": finding.code,
        "severity": finding.severity,
        "message": finding.message,
        "spec_id": getattr(finding, "spec_id", ""),
        "refs": list(getattr(finding, "refs", ())),
    }


def _validate_dsl_blocks_for(document: SpecDocument) -> list[dict[str, Any]]:
    registry = make_default_registry()
    blocks = extract_dsl_blocks(document.body)
    findings: list[dict[str, Any]] = []
    for block in blocks:
        if not registry.is_known(block.kind):
            findings.append(
                {
                    "code": "unknown_dsl_kind",
                    "severity": "warning",
                    "message": f"DSL kind {block.kind!r} is not registered.",
                    "spec_id": document.frontmatter.spec_id,
                    "refs": (),
                    "line_start": block.line_start,
                }
            )
            continue
        result = registry.parse(block)
        if result is None:
            continue
        for finding in result.findings:
            findings.append(
                {
                    "code": f"dsl_{block.kind}",
                    "severity": finding.severity,
                    "message": finding.message,
                    "spec_id": document.frontmatter.spec_id,
                    "refs": (),
                    "line_start": finding.line_start,
                }
            )
    return findings


@router.post("/v1/spec/{spec_id:path}/validate")
def validate_spec_endpoint(
    spec_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    workspace = _resolve_workspace(payload.get("workspace_root"))
    try:
        documents = _load_all_documents(workspace)
    except SpecRepositoryError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception
    by_id = {document.frontmatter.spec_id: document for document in documents}
    if spec_id not in by_id:
        raise HTTPException(status_code=404, detail=f"spec not found: {spec_id}")
    structural = validate_documents(documents)
    serialized: list[dict[str, Any]] = [
        _serialize_finding(finding)
        for finding in structural.findings
        if finding.spec_id == spec_id or spec_id in finding.refs
    ]
    serialized.extend(_validate_dsl_blocks_for(by_id[spec_id]))
    overall_ok = not any(item["severity"] == "error" for item in serialized)
    return {
        "spec_id": spec_id,
        "ok": overall_ok,
        "findings": serialized,
    }


@router.get("/v1/spec/graph")
def get_spec_graph(
    workspace_root: str = Query(...),
    persist: bool = Query(False),
) -> dict[str, Any]:
    workspace = _resolve_workspace(workspace_root)
    try:
        graph = build_workspace_graph(workspace)
    except SpecRepositoryError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception
    payload = graph.to_dict()
    if persist:
        try:
            target_path = write_graph_file(workspace)
            payload["persisted_path"] = str(target_path)
        except Exception as exception:
            raise HTTPException(status_code=500, detail=str(exception)) from exception
    return payload


@router.get("/v1/spec/{spec_id:path}/ancestors")
def get_ancestors(
    spec_id: str,
    workspace_root: str = Query(...),
    depth: int = Query(1, ge=1, le=20),
) -> dict[str, Any]:
    workspace = _resolve_workspace(workspace_root)
    return {
        "spec_id": spec_id,
        "depth": depth,
        "ancestors": list(spec_ancestors(workspace, spec_id, depth=depth)),
    }


@router.get("/v1/spec/{spec_id:path}/dependants")
def get_dependants(
    spec_id: str,
    workspace_root: str = Query(...),
    depth: int = Query(1, ge=1, le=20),
) -> dict[str, Any]:
    workspace = _resolve_workspace(workspace_root)
    return {
        "spec_id": spec_id,
        "depth": depth,
        "dependants": list(spec_dependants(workspace, spec_id, depth=depth)),
    }


@router.get("/v1/spec/orphans")
def get_orphans(
    workspace_root: str = Query(...),
    anchor: str = Query("_project"),
) -> dict[str, Any]:
    workspace = _resolve_workspace(workspace_root)
    return {
        "anchor": anchor,
        "orphans": list(spec_orphans(workspace, anchor=anchor)),
    }


_VERIFIER_NAMES = frozenset({"flake8", "mypy", "pytest"})


def _build_verifiers(names: list[str]) -> tuple[CodeVerifier, ...]:
    from backend.App.spec.infrastructure.verifiers.flake8_verifier import Flake8Verifier
    from backend.App.spec.infrastructure.verifiers.mypy_verifier import MypyVerifier
    from backend.App.spec.infrastructure.verifiers.pytest_verifier import PytestVerifier

    result: list[CodeVerifier] = []
    for name in names:
        if name == "flake8":
            result.append(Flake8Verifier())
        elif name == "mypy":
            result.append(MypyVerifier())
        elif name == "pytest":
            result.append(PytestVerifier())
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown verifier {name!r}. Supported: {sorted(_VERIFIER_NAMES)}",
            )
    return tuple(result)


class CodegenRequestBody(BaseModel):
    workspace_root: str
    model_name: Optional[str] = None
    seed: Optional[int] = None
    verifiers: List[str] = Field(default_factory=list)
    n_best: Optional[int] = None
    selection_strategy: Optional[str] = None


@router.post("/v1/spec/{spec_id:path}/generate")
def generate_spec_endpoint(spec_id: str, body: CodegenRequestBody) -> dict[str, Any]:
    from backend.App.spec.domain.candidate_selection import NoCandidatePassedError

    workspace = _resolve_workspace(body.workspace_root)
    use_n_best = body.n_best is not None and body.n_best > 1
    request = CodegenRequest(
        spec_id=spec_id,
        model_name=body.model_name or "stub",
        seed=body.seed if body.seed is not None else 0,
        use_n_best=use_n_best,
        n_best_n=body.n_best if body.n_best is not None else 3,
        n_best_strategy=body.selection_strategy or "lowest_error",
    )
    verifiers = _build_verifiers(body.verifiers)
    try:
        outcome = run_codegen(workspace, request, verifiers=verifiers)
    except NoCandidatePassedError as exception:
        raise HTTPException(status_code=422, detail=str(exception)) from exception
    except CodegenError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception
    except SpecRepositoryError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception
    return {
        "spec_id": outcome.spec_id,
        "written_files": list(outcome.written_files),
        "sidecar_paths": list(outcome.sidecar_paths),
        "retry_count": outcome.retry_count,
    }


class DifferentialCodegenRequestBody(BaseModel):
    workspace_root: str
    primary_model: str
    alternative_model: str
    seed: Optional[int] = None


@router.post("/v1/spec/{spec_id:path}/generate/differential")
def generate_differential_endpoint(
    spec_id: str, body: DifferentialCodegenRequestBody
) -> dict[str, Any]:
    from backend.App.spec.application.differential_codegen import (
        DifferentialCodegenError,
        run_differential_codegen,
    )
    from backend.App.spec.application.codegen import CodegenRequest, _StubLLMClient

    workspace = _resolve_workspace(body.workspace_root)
    request = CodegenRequest(
        spec_id=spec_id,
        model_name=body.primary_model,
        seed=body.seed if body.seed is not None else 0,
    )
    primary_client = _StubLLMClient()
    alternative_client = _StubLLMClient()
    try:
        outcome = run_differential_codegen(
            request,
            primary_client=primary_client,
            alternative_client=alternative_client,
            primary_model_name=body.primary_model,
            alternative_model_name=body.alternative_model,
            workspace_root=workspace,
        )
    except DifferentialCodegenError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception
    except SpecRepositoryError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception
    report = outcome.report
    return {
        "spec_id": spec_id,
        "primary_model": report.model_a,
        "alternative_model": report.model_b,
        "agreement_ratio": report.agreement_ratio,
        "findings": [
            {
                "kind": f.kind,
                "severity": f.severity,
                "message": f.message,
                "details": f.details,
            }
            for f in report.findings
        ],
        "primary_outcome": {
            "written_files": list(outcome.primary_outcome.written_files),
            "retry_count": outcome.primary_outcome.retry_count,
        },
        "alternative_outcome": {
            "written_files": list(outcome.alternative_outcome.written_files),
            "retry_count": outcome.alternative_outcome.retry_count,
        },
    }


class MutationRequestBody(BaseModel):
    workspace_root: str
    threshold: Optional[float] = None


@router.post("/v1/spec/{spec_id:path}/mutate")
def mutate_spec_endpoint(spec_id: str, body: MutationRequestBody) -> dict[str, Any]:
    from backend.App.spec.domain.mutation_finding import mutation_score
    from backend.App.spec.infrastructure.verifiers.mutation_verifier import (
        MutationVerifier,
        MutationVerifierError,
    )

    workspace = _resolve_workspace(body.workspace_root)
    try:
        repository = FilesystemSpecRepository(workspace)
        document = repository.load(spec_id)
    except SpecNotFoundError as exception:
        raise HTTPException(status_code=404, detail=str(exception)) from exception
    except SpecRepositoryError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception

    targets = tuple(document.frontmatter.codegen_targets)
    if not targets:
        raise HTTPException(
            status_code=400,
            detail=f"spec {spec_id!r} has no codegen_targets to mutate",
        )

    verifier = MutationVerifier(threshold=body.threshold)
    try:
        stats_tuple = verifier.run(Path(workspace), targets)
    except MutationVerifierError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception

    effective_threshold = (
        body.threshold if body.threshold is not None else verifier._resolve_threshold()
    )
    return {
        "spec_id": spec_id,
        "threshold": effective_threshold,
        "stats": [
            {
                "target_path": s.target_path,
                "mutants_total": s.mutants_total,
                "mutants_killed": s.mutants_killed,
                "mutants_survived": s.mutants_survived,
                "score": mutation_score(s),
                "below_threshold": mutation_score(s) < effective_threshold,
            }
            for s in stats_tuple
        ],
    }


@router.get("/v1/spec/drift")
def drift_endpoint(workspace_root: str = Query(...)) -> dict[str, Any]:
    workspace = _resolve_workspace(workspace_root)
    try:
        report = detect_drift(workspace)
    except Exception as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception
    return {
        "stale_code": [
            {
                "spec_id": entry.spec_id,
                "target_path": entry.target_path,
                "spec_hash": entry.spec_hash,
                "sidecar_hash": entry.sidecar_hash,
            }
            for entry in report.stale_code
        ],
        "stale_specs": [
            {
                "spec_id": entry.spec_id,
                "target_path": entry.target_path,
                "spec_hash": entry.spec_hash,
                "sidecar_hash": entry.sidecar_hash,
            }
            for entry in report.stale_specs
        ],
        "aged_keep_regions": [
            {
                "spec_id": r.spec_id,
                "target_path": r.target_path,
                "reason": r.reason,
                "added_at": r.added_at,
                "age_days": r.age_days,
            }
            for r in report.aged_keep_regions
        ],
    }


@router.post("/v1/spec/extract")
def extract_spec_endpoint(request: SpecExtractRequest) -> dict[str, Any]:
    workspace = _resolve_workspace(request.workspace_root)
    try:
        document = extract_spec_from_code(
            workspace,
            request.code_path,
            spec_id_override=request.spec_id_override,
            save=request.save,
        )
    except ExtractError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception
    return {
        "spec": _serialize_document(document),
        "saved": request.save,
    }


class SketchFillRequest(BaseModel):
    workspace_root: str
    sketch_text: str
    model_name: Optional[str] = None


@router.post("/v1/spec/{spec_id:path}/sketch-fill")
def sketch_fill_endpoint(spec_id: str, body: SketchFillRequest) -> dict[str, Any]:
    from backend.App.spec.application.sketch_codegen import (
        SketchCodegenError,
        run_sketch_codegen,
    )
    from backend.App.spec.application.codegen import CodegenRequest, _StubLLMClient

    _resolve_workspace(body.workspace_root)
    request = CodegenRequest(
        spec_id=spec_id,
        model_name=body.model_name or "stub",
    )
    llm_client = _StubLLMClient()
    try:
        outcome = run_sketch_codegen(request, body.sketch_text, llm_client=llm_client)
    except SketchCodegenError as exception:
        raise HTTPException(status_code=400, detail=str(exception)) from exception
    return {
        "filled_source": outcome.filled_source,
        "holes_filled": outcome.holes_filled,
        "hole_qualnames": list(outcome.hole_qualnames),
    }


@router.get("/v1/postmortems")
def list_postmortems(
    spec_id: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),
    failure_kind: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    k: int = Query(10, ge=1, le=100),
) -> dict[str, Any]:
    from backend.App.integrations.application.postmortem_retrieval import retrieve_postmortems
    from backend.App.integrations.domain.postmortem import PostmortemQuery, serialise_postmortem
    from backend.App.integrations.infrastructure.qdrant_client import get_vector_store
    from backend.App.integrations.infrastructure.embedding_service import get_embedding_provider

    query = PostmortemQuery(
        spec_id=spec_id,
        agent=agent,
        failure_kind=failure_kind,
        tag=tag,
        k=k,
    )
    query_text = " ".join(filter(None, [spec_id, agent, failure_kind, tag]))
    vector_store = get_vector_store()
    embedding_provider = get_embedding_provider()
    postmortems = retrieve_postmortems(query, vector_store, embedding_provider, query_text)
    return {
        "postmortems": [serialise_postmortem(pm) for pm in postmortems],
        "count": len(postmortems),
    }


__all__ = ["router"]

"""Pipeline state type and related constants.

Extracted from ``pipeline.graph`` to keep the composition layer thin.
All names are re-exported from ``pipeline.graph`` for backward compatibility.
"""

from __future__ import annotations

from typing import Any, TypedDict


class WorkspaceIdentityState(TypedDict, total=False):
    workspace_root: str
    workspace_root_resolved: str
    project_manifest_hash: str
    workspace_snapshot_hash: str


class ClarifyInputCacheState(TypedDict, total=False):
    hit: bool
    cache_key: str
    reuse_blocked_reason: str
    identity: dict[str, str]


class RepoEvidenceEntryState(TypedDict, total=False):
    path: str
    start_line: int
    end_line: int
    excerpt: str
    why: str
    hash: str
    preview: str
    size: int
    excerpt_sha256: str
    excerpt_repaired: bool
    model_excerpt: str


class PlaceholderAllowEntryState(TypedDict, total=False):
    path: str
    pattern: str
    reason: str


class VerificationGateState(TypedDict, total=False):
    passed: bool
    gate_name: str
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    raw_stdout: str
    raw_stderr: str
    details: dict[str, Any]


class MCPWriteActionState(TypedDict, total=False):
    path: str
    mode: str


class DeliverableWriteMappingState(TypedDict, total=False):
    required_path: str
    matched: bool
    producers: list[dict[str, Any]]


class DefectState(TypedDict, total=False):
    id: str
    title: str
    severity: str
    category: str
    file_paths: list[str]


class DefectClusterState(TypedDict, total=False):
    cluster_key: str
    category: str
    count: int
    severity: str
    defect_ids: list[str]
    titles: list[str]
    file_paths: list[str]


class PipelineState(TypedDict, total=False):
    input: str
    agent_config: dict[str, Any]
    # Разбор исходного запроса (оркестратор); для legacy-снимков может отсутствовать — см. pipeline_user_task()
    user_task: str
    project_manifest: str
    # Тело секции workspace до склейки (полный снимок, индекс путей или приоритетные файлы)
    workspace_snapshot: str
    # full | index_only | priority_paths | post_analysis_compact | tools_only | retrieve
    workspace_context_mode: str
    # Заголовок секции в input: «Workspace snapshot» или «Workspace index»
    workspace_section_title: str
    # retrieve без MCP после prepare_workspace — подставлен индекс путей
    workspace_context_mcp_fallback: bool
    # Локальный проект на машине оркестратора (путь строкой); пусто = нет
    workspace_root: str
    workspace_root_resolved: str
    workspace_identity: WorkspaceIdentityState
    workspace_apply_writes: bool
    # Wiki context loaded from <workspace_root>/.swarm/wiki/ (markdown index + articles)
    wiki_context: str
    # Идентификатор задачи оркестратора (артефакты artifacts/<task_id>/)
    task_id: str
    # Результат analyze_workspace (swarm_code_analysis/v1)
    code_analysis: dict[str, Any]
    # SWARM_DOC_FETCH: манифест загруженных URL → файлы в artifacts / .swarm/doc_fetch
    doc_fetch_manifest: list[dict[str, Any]]
    # Clarify input (pre-pipeline requirement clarification)
    clarify_input_output: str
    clarify_input_model: str
    clarify_input_provider: str
    clarify_input_human_output: str
    clarify_input_cache: ClarifyInputCacheState
    planning_review_feedback: dict[str, str]
    planning_review_blockers: list[dict[str, Any]]
    # PM
    pm_output: str
    pm_model: str
    pm_provider: str
    pm_memory_artifact: dict[str, Any]
    pm_review_output: str
    pm_review_model: str
    pm_review_provider: str
    pm_human_output: str
    # BA
    ba_output: str
    ba_model: str
    ba_provider: str
    ba_memory_artifact: dict[str, Any]
    ba_repo_evidence: list[RepoEvidenceEntryState]
    ba_unverified_claims: list[str]
    ba_review_output: str
    ba_review_model: str
    ba_review_provider: str
    ba_human_output: str
    # Architect
    arch_output: str
    arch_model: str
    arch_provider: str
    arch_memory_artifact: dict[str, Any]
    stack_review_output: str
    stack_review_model: str
    stack_review_provider: str
    arch_review_output: str
    arch_review_model: str
    arch_review_provider: str
    arch_human_output: str
    # DebateWithJudge: BA↔Architect reconciliation (опциональный шаг ba_arch_debate)
    ba_arch_debate_output: str
    ba_arch_debate_model: str
    ba_arch_debate_provider: str
    # Merge + spec review
    spec_output: str
    spec_memory_artifact: dict[str, Any]
    spec_review_output: str
    spec_review_model: str
    spec_review_provider: str
    spec_human_output: str
    # Design: UX Research → UX Architecture → UI Design
    ux_researcher_output: str
    ux_researcher_model: str
    ux_researcher_provider: str
    ux_researcher_review_output: str
    ux_researcher_review_model: str
    ux_researcher_review_provider: str
    ux_researcher_human_output: str
    ux_architect_output: str
    ux_architect_model: str
    ux_architect_provider: str
    ux_architect_review_output: str
    ux_architect_review_model: str
    ux_architect_review_provider: str
    ux_architect_human_output: str
    ui_designer_output: str
    ui_designer_model: str
    ui_designer_provider: str
    ui_designer_review_output: str
    ui_designer_review_model: str
    ui_designer_review_provider: str
    ui_designer_human_output: str
    # Анализ кода и производные (между human_spec и devops)
    analyze_code_output: str
    code_diagram_output: str
    code_diagram_model: str
    code_diagram_provider: str
    generate_documentation_output: str
    generate_documentation_model: str
    generate_documentation_provider: str
    # Пути под workspace_root, куда шаг generate_documentation записал markdown (если было)
    documentation_workspace_files: list[str]
    problem_spotter_output: str
    problem_spotter_model: str
    problem_spotter_provider: str
    problem_spotter_repo_evidence: list[RepoEvidenceEntryState]
    problem_spotter_unverified_claims: list[str]
    refactor_plan_output: str
    refactor_plan_model: str
    refactor_plan_provider: str
    refactor_plan_repo_evidence: list[RepoEvidenceEntryState]
    refactor_plan_unverified_claims: list[str]
    code_review_human_output: str
    # DevOps: bootstrap / зависимости до Dev
    devops_output: str
    devops_model: str
    devops_provider: str
    devops_review_output: str
    devops_review_model: str
    devops_review_provider: str
    devops_human_output: str
    # Dev Lead: план подзадач Dev/QA после spec (legacy ключи pm_tasks_* мигрируют при resume)
    dev_lead_output: str
    dev_lead_model: str
    dev_lead_provider: str
    dev_lead_review_output: str
    dev_lead_review_model: str
    dev_lead_review_provider: str
    dev_lead_human_output: str
    dev_qa_tasks: list[dict[str, Any]]
    dev_task_outputs: list[str]
    qa_task_outputs: list[str]
    # K-9: per-step retry counters and feedback lists (used by adaptive_retry helpers)
    step_retries: dict[str, int]
    step_feedback: dict[str, list[str]]
    pipeline_phase: str
    pipeline_machine: dict[str, Any]
    deliverables_artifact: dict[str, Any]
    must_exist_files: list[str]
    spec_symbols: list[str]
    production_paths: list[str]
    placeholder_allow_list: list[PlaceholderAllowEntryState]
    arch_repo_evidence: list[RepoEvidenceEntryState]
    arch_unverified_claims: list[str]
    devops_repo_evidence: list[RepoEvidenceEntryState]
    devops_unverified_claims: list[str]
    dev_manifest: dict[str, Any]
    verification_contract: dict[str, Any]
    verification_gates: list[VerificationGateState]
    pipeline_metrics: dict[str, Any]
    deliverable_write_mapping: list[DeliverableWriteMappingState]
    dev_defect_report: dict[str, Any]
    qa_defect_report: dict[str, Any]
    qa_review_defect_report: dict[str, Any]
    open_defects: list[DefectState]
    clustered_open_defects: list[DefectClusterState]
    # Dev
    dev_output: str
    dev_model: str
    dev_provider: str
    dev_mcp_write_actions: list[MCPWriteActionState]
    dev_subtask_contracts: list[dict[str, Any]]
    dev_review_output: str
    dev_review_model: str
    dev_review_provider: str
    dev_human_output: str
    # QA
    qa_output: str
    qa_model: str
    qa_provider: str
    qa_review_output: str
    qa_review_model: str
    qa_review_provider: str
    qa_human_output: str
    # Marketing: SEO → AI Citation → App Store Optimization
    seo_specialist_output: str
    seo_specialist_model: str
    seo_specialist_provider: str
    seo_specialist_review_output: str
    seo_specialist_review_model: str
    seo_specialist_review_provider: str
    seo_specialist_human_output: str
    ai_citation_strategist_output: str
    ai_citation_strategist_model: str
    ai_citation_strategist_provider: str
    ai_citation_strategist_review_output: str
    ai_citation_strategist_review_model: str
    ai_citation_strategist_review_provider: str
    ai_citation_strategist_human_output: str
    app_store_optimizer_output: str
    app_store_optimizer_model: str
    app_store_optimizer_provider: str
    app_store_optimizer_review_output: str
    app_store_optimizer_review_model: str
    app_store_optimizer_review_provider: str
    app_store_optimizer_human_output: str
    # E2E
    e2e_output: str
    e2e_status: str
    e2e_artifacts_dir: str


# Pipeline-level input keys that are initialised explicitly in run_pipeline()
# rather than bulk-zeroed to "".  Excluded from _PIPELINE_STRING_KEYS.
_PIPELINE_INPUT_KEYS: frozenset[str] = frozenset(
    {
        "input",
        "workspace_root",
        "workspace_root_resolved",
        "workspace_identity",
        "task_id",
        "user_task",
        "project_manifest",
        "workspace_snapshot",
        "workspace_context_mode",
        "workspace_section_title",
        "workspace_context_mcp_fallback",
    }
)

# Имена agents/<name>.txt и ключи PipelineState (non-stream артефакты).
# Пустые строки по умолчанию — чтобы кастомный порядок шагов не давал KeyError.
#
# Derived automatically from PipelineState.__annotations__: all str-typed keys
# that are not pipeline-level inputs.  Adding a new str key to PipelineState is
# sufficient — no manual update required here.


def _derive_pipeline_string_keys() -> tuple[str, ...]:
    return tuple(
        k
        for k, v in PipelineState.__annotations__.items()
        if getattr(v, "__forward_arg__", None) == "str"
        and k not in _PIPELINE_INPUT_KEYS
    )


_PIPELINE_STRING_KEYS: tuple[str, ...] = _derive_pipeline_string_keys()

# ARTIFACT_AGENT_OUTPUT_KEYS maps artifact filename stems (agents/<name>.txt)
# to their PipelineState output keys.  The filename stem is NOT derivable from
# the state key (e.g. "review_pm" → "pm_review_output"), so this mapping must
# remain explicit.  New agent steps require a manual entry here.
ARTIFACT_AGENT_OUTPUT_KEYS: tuple[tuple[str, str], ...] = (
    ("clarify_input", "clarify_input_output"),
    ("human_clarify_input", "clarify_input_human_output"),
    ("pm", "pm_output"),
    ("review_pm", "pm_review_output"),
    ("human_pm", "pm_human_output"),
    ("ba", "ba_output"),
    ("review_ba", "ba_review_output"),
    ("human_ba", "ba_human_output"),
    ("architect", "arch_output"),
    ("review_stack", "stack_review_output"),
    ("review_arch", "arch_review_output"),
    ("human_arch", "arch_human_output"),
    ("ba_arch_debate", "ba_arch_debate_output"),
    ("spec_merge", "spec_output"),
    ("review_spec", "spec_review_output"),
    ("human_spec", "spec_human_output"),
    ("ux_researcher", "ux_researcher_output"),
    ("review_ux_researcher", "ux_researcher_review_output"),
    ("human_ux_researcher", "ux_researcher_human_output"),
    ("ux_architect", "ux_architect_output"),
    ("review_ux_architect", "ux_architect_review_output"),
    ("human_ux_architect", "ux_architect_human_output"),
    ("ui_designer", "ui_designer_output"),
    ("review_ui_designer", "ui_designer_review_output"),
    ("human_ui_designer", "ui_designer_human_output"),
    ("analyze_code", "analyze_code_output"),
    ("generate_documentation", "generate_documentation_output"),
    ("problem_spotter", "problem_spotter_output"),
    ("refactor_plan", "refactor_plan_output"),
    ("human_code_review", "code_review_human_output"),
    ("devops", "devops_output"),
    ("review_devops", "devops_review_output"),
    ("human_devops", "devops_human_output"),
    ("dev_lead", "dev_lead_output"),
    ("review_dev_lead", "dev_lead_review_output"),
    ("human_dev_lead", "dev_lead_human_output"),
    ("dev", "dev_output"),
    ("review_dev", "dev_review_output"),
    ("human_dev", "dev_human_output"),
    ("qa", "qa_output"),
    ("review_qa", "qa_review_output"),
    ("human_qa", "qa_human_output"),
    ("seo_specialist", "seo_specialist_output"),
    ("review_seo_specialist", "seo_specialist_review_output"),
    ("human_seo_specialist", "seo_specialist_human_output"),
    ("ai_citation_strategist", "ai_citation_strategist_output"),
    ("review_ai_citation_strategist", "ai_citation_strategist_review_output"),
    ("human_ai_citation_strategist", "ai_citation_strategist_human_output"),
    ("app_store_optimizer", "app_store_optimizer_output"),
    ("review_app_store_optimizer", "app_store_optimizer_review_output"),
    ("human_app_store_optimizer", "app_store_optimizer_human_output"),
    ("e2e", "e2e_output"),
)

# ---------------------------------------------------------------------------
# Sanity checks (run at import time)
# ---------------------------------------------------------------------------

# Every state key referenced in ARTIFACT_AGENT_OUTPUT_KEYS must exist in
# PipelineState.  Catches typos when adding new agent steps.
_all_state_keys: frozenset[str] = frozenset(PipelineState.__annotations__)
_missing_artifact_keys = [
    key
    for _, key in ARTIFACT_AGENT_OUTPUT_KEYS
    if key not in _all_state_keys
]
assert not _missing_artifact_keys, (
    f"ARTIFACT_AGENT_OUTPUT_KEYS references state keys not defined in "
    f"PipelineState: {_missing_artifact_keys}"
)

# Every key in ARTIFACT_AGENT_OUTPUT_KEYS must also be in _PIPELINE_STRING_KEYS
# (artifact outputs are always str fields initialised to "").
_missing_string_keys = [
    key
    for _, key in ARTIFACT_AGENT_OUTPUT_KEYS
    if key not in _PIPELINE_STRING_KEYS
]
assert not _missing_string_keys, (
    f"ARTIFACT_AGENT_OUTPUT_KEYS contains keys absent from _PIPELINE_STRING_KEYS: "
    f"{_missing_string_keys}"
)

del _all_state_keys, _missing_artifact_keys, _missing_string_keys


# ---------------------------------------------------------------------------
# pipeline_workspace_parts_from_meta
# ---------------------------------------------------------------------------

def pipeline_workspace_parts_from_meta(meta_ws: dict[str, Any]) -> dict[str, Any]:
    """Build pipeline workspace fields from prepare_workspace metadata.

    Returns the subset of PipelineState keys that describe the workspace context,
    suitable for passing to ``run_pipeline`` / ``_initial_pipeline_state``.
    """
    from backend.App.workspace.domain.ports import WORKSPACE_CONTEXT_MODE_DEFAULT

    return {
        "user_task": str(meta_ws.get("user_task") or ""),
        "project_manifest": str(meta_ws.get("project_manifest") or ""),
        "workspace_snapshot": str(meta_ws.get("workspace_snapshot") or ""),
        "workspace_root_resolved": str(meta_ws.get("workspace_root_resolved") or ""),
        "workspace_context_mode": str(
            meta_ws.get("workspace_context_mode") or WORKSPACE_CONTEXT_MODE_DEFAULT
        ),
        "workspace_section_title": str(
            meta_ws.get("workspace_section_title") or "Workspace snapshot"
        ),
        "workspace_context_mcp_fallback": bool(meta_ws.get("workspace_context_mcp_fallback")),
    }

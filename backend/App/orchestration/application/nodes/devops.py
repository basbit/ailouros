from __future__ import annotations

import logging
import os
from typing import Any

from backend.App.orchestration.infrastructure.agents.devops_agent import DevopsAgent
from backend.App.orchestration.application.agents.review_moa import run_reviewer_or_moa
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.application.context.repo_evidence import (
    ensure_validated_repo_evidence,
    format_repo_evidence_for_prompt,
)
from backend.App.orchestration.application.nodes._shared import (
    _cfg_model,
    _code_analysis_is_weak,
    _compact_code_analysis_for_prompt,
    _dev_workspace_instructions,
    _documentation_locale_line,
    _effective_spec_for_build,
    _llm_planning_agent_run,
    _make_human_agent,
    _make_reviewer_agent,
    _pipeline_context_block,
    _project_knowledge_block,
    _should_use_mcp_for_workspace,
    _spec_for_build_mcp_safe,
    planning_mcp_tool_instruction,
    _remote_api_client_kwargs_for_role,
    _skills_extra_for_role_cfg,
    _swarm_prompt_prefix,
    build_phase_pipeline_user_context,
    pipeline_user_task,
    embedded_pipeline_input_for_review,
    embedded_review_artifact,
)

logger = logging.getLogger(__name__)


def devops_node(state: PipelineState) -> dict[str, Any]:
    agent_config = state.get("agent_config") or {}
    cfg = agent_config.get("devops") or agent_config.get("dev") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    agent = DevopsAgent(
        system_prompt_path_override=cfg.get("prompt_path") or cfg.get("prompt"),
        model_override=_cfg_model(cfg),
        environment_override=cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, cfg),
        **_remote_api_client_kwargs_for_role(state, cfg),
    )
    ws = _dev_workspace_instructions(state)
    ctx = _pipeline_context_block(state, "devops")
    _ca_raw = state.get("code_analysis")
    code_analysis: dict[str, Any] = _ca_raw if isinstance(_ca_raw, dict) else {}
    use_mcp = _should_use_mcp_for_workspace(state)
    ca_block = ""
    if not use_mcp and not _code_analysis_is_weak(code_analysis):
        ca_block = "[Existing code analysis]\n" + _compact_code_analysis_for_prompt(code_analysis, max_chars=6000) + "\n\n"
    prompt = (
        ctx
        + _swarm_prompt_prefix(state)
        + _documentation_locale_line(state)
        + planning_mcp_tool_instruction(state)
        + _project_knowledge_block(state, step_id="devops")
        + "[Pipeline rule] Based on the **approved specification** (below), prepare the "
        "**operational bootstrap**: discover the real project commands, validate local "
        "prerequisites, and provide executable launch/build/smoke commands. The stack "
        "comes from repository evidence and the Architect section.\n"
        "Commands for **auto-execution** on the host — only in `<swarm_shell>` or `<swarm-command>` "
        "**outside** `` ``` `` fences; inside fences the orchestrator will NOT execute them.\n\n"
        "For runnable application tasks, do not stop at CI/CD prose. Include the command(s) "
        "that should actually launch, build, or smoke-test the app from the workspace. "
        "If execution is impossible, return `BLOCKED` with the exact missing tool, path, "
        "credential, emulator/device, or approval.\n\n"
        "[CRITICAL — emit infrastructure FILES, not only prose] In addition to the "
        "runbook, you MUST write actual artifact files to the workspace using "
        "`<swarm_file path='…'>full content</swarm_file>` blocks. These files persist in the "
        "repo so the user has working infrastructure even when shell commands are blocked or "
        "the host lacks SDKs. Include AT MINIMUM the following when applicable to the stack:\n"
        "  - `.gitignore` — tailored to the actual stack (e.g. for Unity: Library/, Temp/, "
        "obj/, *.csproj.user, *.unityproj; for Node: node_modules/, dist/; for Python: "
        "__pycache__/, .venv/, *.pyc).\n"
        "  - Build/launch scripts — e.g. `build_android.sh`, `build_ios.sh`, `run_dev.sh`, "
        "`smoke_test.sh`. Make them executable shell scripts with explicit error handling "
        "(`set -euo pipefail`).\n"
        "  - CI workflow file — e.g. `.github/workflows/ci.yml` or equivalent for the user's "
        "git host, wired to the build/test scripts above.\n"
        "  - `Dockerfile` for any backend services you described.\n"
        "  - `README.md` (or `OPERATIONS.md`) summarising prerequisites, install, run, build, "
        "test, deploy commands — the same ones referenced in shell tags.\n"
        "  - Lockfiles or stub package descriptors when the stack requires them "
        "(e.g. `package.json` for Node tooling, `pyproject.toml` for Python helpers, "
        "`Packages/manifest.json` for Unity).\n"
        "Do NOT skip emitting these files just because shell execution may be blocked on "
        "this host — the workspace files are the deliverable; shell commands are the "
        "verification step. If a particular file is genuinely not applicable, explicitly "
        "say so and explain why.\n\n"
        f"User task:\n{pipeline_user_task(state) if use_mcp else build_phase_pipeline_user_context(state)}\n\n"
        f"Specification:\n{_spec_for_build_mcp_safe(state)}\n\n"
        f"{ca_block}"
        "Evidence contract:\n"
        "If you claim that the repository already uses a runtime, package manager, test runner, "
        "build system, deployment mechanism, or existing automation, add a final ```json``` block "
        "with this schema:\n"
        '{'
        '"repo_evidence":[{"path":"relative/path","start_line":1,"end_line":3,'
        '"excerpt":"exact text copied from the repository","why":"what this proves"}],'
        '"unverified_claims":["claim that cannot be proven from the repository yet"]'
        '}\n'
        "Rules:\n"
        "- Every repo-based tech claim must be backed by `repo_evidence` or moved to `unverified_claims`.\n"
        "- `excerpt` must exactly match the referenced file lines.\n"
        "- Do not omit the JSON block.\n"
    )
    prompt += ws
    devops_result, _, _ = _llm_planning_agent_run(agent, prompt, state)
    _devops_min = int(os.environ.get("SWARM_DEVOPS_MIN_OUTPUT_CHARS", "80"))
    if len((devops_result or "").strip()) < _devops_min:
        logger.warning(
            "devops_node: output too short (%d chars < %d min) — retrying once. task_id=%s",
            len((devops_result or "").strip()),
            _devops_min,
            str(state.get("task_id") or "")[:36],
        )
        _retry_prompt = (
            "The previous attempt produced no output. Please try again.\n\n"
            "Describe the bootstrap steps (dependency install, project init) "
            "and provide any needed setup commands in <swarm_shell> tags. "
            "Keep the response concise.\n\n"
            + prompt
        )
        devops_result, _, _ = _llm_planning_agent_run(agent, _retry_prompt, state)
    devops_result, validated_repo_evidence = ensure_validated_repo_evidence(
        raw_output=devops_result,
        base_prompt=prompt,
        workspace_root=str(state.get("workspace_root") or ""),
        step_id="devops_node",
        retry_run=lambda retry_prompt: _llm_planning_agent_run(agent, retry_prompt, state)[0],
    )
    if (devops_result or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "devops", devops_result)
    return {
        "devops_output": devops_result,
        "devops_model": agent.used_model,
        "devops_provider": agent.used_provider,
        "devops_repo_evidence": validated_repo_evidence.get("repo_evidence") or [],
        "devops_unverified_claims": validated_repo_evidence.get("unverified_claims") or [],
    }


def review_devops_node(state: PipelineState) -> dict[str, Any]:
    user_block = embedded_pipeline_input_for_review(state, log_node="review_devops_node")
    spec_full = _effective_spec_for_build(state)
    spec_art = embedded_review_artifact(
        state,
        spec_full,
        log_node="review_devops_node",
        part_name="specification",
        env_name="SWARM_REVIEW_SPEC_MAX_CHARS",
        default_max=40_000,
        mcp_max=3_000,
    )
    devops_art = embedded_review_artifact(
        state,
        state.get("devops_output"),
        log_node="review_devops_node",
        part_name="devops_output",
        env_name="SWARM_REVIEW_DEVOPS_OUTPUT_MAX_CHARS",
        default_max=60_000,
        mcp_max=4_000,
    )
    repo_evidence_artifact = {
        "repo_evidence": list(state.get("devops_repo_evidence") or []),
        "unverified_claims": list(state.get("devops_unverified_claims") or []),
    }
    execution_contract = state.get("devops_execution_contract")
    execution_contract_text = (
        str(execution_contract)
        if isinstance(execution_contract, dict)
        else "{}"
    )
    prompt = (
        "Step: devops (bootstrap, dependencies, runbook after spec).\n"
        "Checklist — issue VERDICT: NEEDS_WORK if ANY item fails:\n"
        "[ ] All shell commands use <swarm_shell> tags (not fenced code blocks)\n"
        "[ ] Runnable/write-capable tasks include actual launch/build/smoke command evidence, not only CI/CD prose\n"
        "[ ] A numbered runbook of the same commands is present after <swarm_shell> blocks\n"
        "[ ] Commands are realistic for the Architect stack (no wrong package manager/runtime)\n"
        "[ ] Repo-based runtime/build/test/tooling claims are backed by validated repo_evidence or explicitly marked unverified\n"
        "[ ] No heavy platform automation (e.g. full cluster/IaC, full CI matrix, security suites) unless explicitly in the spec\n"
        "[ ] E2E/UI tooling matches the **declared** stack (do not assume browser automation for native-only specs)\n\n"
        f"User task:\n{user_block}\n\n"
        f"Specification:\n{spec_art}\n\n"
        "Validated repo evidence artifact:\n"
        f"{format_repo_evidence_for_prompt(repo_evidence_artifact)}\n\n"
        "DevOps execution contract:\n"
        f"{execution_contract_text}\n\n"
        f"DevOps output:\n{devops_art}"
    )
    return run_reviewer_or_moa(
        state,
        pipeline_step="review_devops",
        prompt=prompt,
        output_key="devops_review_output",
        model_key="devops_review_model",
        provider_key="devops_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
    )


def human_devops_node(state: PipelineState) -> dict[str, Any]:
    bundle = (
        f"DevOps:\n{state['devops_output']}\n\n"
        f"Review:\n{state['devops_review_output']}"
    )
    agent = _make_human_agent(state, "devops")
    return {"devops_human_output": agent.run(bundle)}

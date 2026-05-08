from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.App.orchestration.infrastructure.agents.code_workflow_agents import (
    ProblemSpotterAgent,
    RefactorPlanAgent,
)
from backend.App.workspace.infrastructure.code_analysis.scan import analysis_to_json, analyze_workspace
from backend.App.workspace.application.doc_workspace import write_step_wiki
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.application.context.repo_evidence import (
    ensure_validated_repo_evidence,
    format_repo_evidence_for_prompt,
)

from backend.App.orchestration.application.nodes._shared import (
    _compact_code_analysis_for_prompt,
    _documentation_locale_line,
    _documentation_product_context_block,
    _doc_generate_second_pass_analysis_max_chars,
    _effective_spec_block_for_doc_chain,
    embedded_review_artifact,
    _llm_agent_run_with_optional_mcp,
    _make_human_agent,
    _remote_api_client_kwargs_for_role,
    _should_use_mcp_for_workspace,
    _skills_extra_for_role_cfg,
    _swarm_block,
    _swarm_languages_line,
    _swarm_prompt_prefix,
)
from backend.App.orchestration.application.nodes._prompt_builders import (
    _prompt_fragment,
    _run_agent_with_boundary,
)
from string import Template

logger = logging.getLogger(__name__)


def _write_plan_artifact(state: "PipelineState", filename: str, content: str) -> None:
    task_id = (state.get("task_id") or "").strip()
    if not task_id or not (content or "").strip():
        return
    from backend.App.paths import artifacts_root as _anchored_artifacts_root
    art_root = _anchored_artifacts_root()
    dest = art_root / task_id / filename
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        logger.info("plan artifact written: %s (%d chars) task_id=%s", dest, len(content), task_id)
    except OSError as exc:
        logger.warning("_write_plan_artifact: failed to write %s: %s", dest, exc)


_CONTEXT_CONFIG_FILE_NAMES: frozenset[str] = frozenset({
    "pyproject.toml", "setup.py", "requirements.txt",
    "package.json", "Cargo.toml", "go.mod", "pom.xml",
    "composer.json", "Gemfile", "build.gradle", "CMakeLists.txt",
})
_CONTEXT_ENTRY_POINT_NAMES: frozenset[str] = frozenset({
    "main.py", "app.py", "index.py", "manage.py", "server.py",
    "index.ts", "index.js", "main.ts", "main.js",
})
_CONTEXT_IGNORE_DIRS: frozenset[str] = frozenset({
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".swarm",
})


def _write_project_context_md(root: Path, payload: dict[str, Any]) -> None:
    try:
        stats = payload.get("stats") or {}
        by_lang: dict[str, int] = stats.get("by_language") or {}
        scanned: int = stats.get("scanned_files", 0)
        root_str = str(payload.get("root") or root)

        files_list: list[dict[str, Any]] = payload.get("files") or []
        config_found = sorted({
            f["path"] for f in files_list
            if Path(f.get("path", "")).name in _CONTEXT_CONFIG_FILE_NAMES
        })
        for cfn in _CONTEXT_CONFIG_FILE_NAMES:
            if (root / cfn).is_file() and cfn not in config_found:
                config_found.append(cfn)

        try:
            top_dirs = sorted(
                d.name for d in root.iterdir()
                if d.is_dir() and not d.name.startswith(".") and d.name not in _CONTEXT_IGNORE_DIRS
            )
        except OSError:
            top_dirs = []

        entries = sorted({
            f["path"] for f in files_list
            if Path(f.get("path", "")).name in _CONTEXT_ENTRY_POINT_NAMES
        })

        lines = [
            "# Project Context\n",
            f"\n**Root:** `{root_str}`\n",
            f"\n**Scanned files:** {scanned}\n",
        ]
        if by_lang:
            lines.append("\n## Languages\n")
            for lang, count in sorted(by_lang.items(), key=lambda x: -x[1]):
                lines.append(f"- **{lang}**: {count} files\n")
        if config_found:
            lines.append("\n## Config files\n")
            for cf in config_found[:20]:
                lines.append(f"- `{cf}`\n")
        if top_dirs:
            lines.append("\n## Top-level structure\n")
            for d in top_dirs[:20]:
                lines.append(f"- `{d}/`\n")
        if entries:
            lines.append("\n## Entry points\n")
            for ep in entries[:10]:
                lines.append(f"- `{ep}`\n")
        lines.append(
            "\n*Auto-generated after full code analysis. Updated on each analyze_code run.*\n"
        )

        swarm_dir = root / ".swarm"
        swarm_dir.mkdir(exist_ok=True)
        (swarm_dir / "project-context.md").write_text("".join(lines), encoding="utf-8")
        logger.debug("analyze_code: wrote .swarm/project-context.md for %s", root)
    except Exception as exc:
        logger.warning("analyze_code: failed to write .swarm/project-context.md: %s", exc)


_PROJECT_ROOT_MARKERS = (
    "pyproject.toml", "setup.py", "setup.cfg",
    "package.json", "Cargo.toml", "go.mod", "pom.xml",
    "composer.json", "Gemfile", "build.gradle",
    "backend", "src", "app", "lib",
)


def analyze_code_node(state: PipelineState) -> dict[str, Any]:
    workspace_root = (state.get("workspace_root") or "").strip()
    task_id = (state.get("task_id") or "").strip()
    swarm_section = _swarm_block(state)
    langs = swarm_section.get("languages") if isinstance(swarm_section.get("languages"), list) else None
    ts_off = bool(swarm_section.get("disable_tree_sitter") or swarm_section.get("tree_sitter_disable"))

    if workspace_root:
        root_check = Path(workspace_root).expanduser()
        if root_check.is_dir():
            has_marker = any((root_check / m).exists() for m in _PROJECT_ROOT_MARKERS)
            if not has_marker:
                logger.warning(
                    "analyze_code: workspace_root '%s' has none of the expected project "
                    "markers (%s). This may be the wrong directory. "
                    "Check the workspace_root setting in the UI.",
                    workspace_root,
                    ", ".join(_PROJECT_ROOT_MARKERS[:6]),
                )
        else:
            logger.warning(
                "analyze_code: workspace_root '%s' does not exist or is not a directory.",
                workspace_root,
            )

    if not workspace_root:
        empty: dict[str, Any] = {
            "schema": "swarm_code_analysis/v1",
            "root": "",
            "files": [],
            "relation_graph": {"schema": "swarm_relation_graph/v1", "edges": [], "nodes": []},
            "stats": {},
            "note": "workspace_root_empty",
        }
        return {
            "code_analysis": empty,
            "analyze_code_output": "workspace_root missing — code analysis skipped.",
        }

    root = Path(workspace_root).expanduser()
    payload = analyze_workspace(
        root, languages_filter=langs, tree_sitter_disabled=ts_off
    )
    analysis_json = analysis_to_json(payload)
    from backend.App.paths import artifacts_root as _anchored_artifacts_root
    art_root = _anchored_artifacts_root()
    if task_id:
        dest = art_root / task_id
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "code_analysis.json").write_text(analysis_json, encoding="utf-8")
        summary = (
            f"Wrote {dest / 'code_analysis.json'}; "
            f"files scanned: {payload.get('stats', {}).get('scanned_files', 0)}"
        )
    else:
        summary = (
            "task_id empty — JSON not written to disk; "
            f"files scanned: {payload.get('stats', {}).get('scanned_files', 0)}"
        )

    _write_project_context_md(root, payload)

    _MAX_STATE_FILES = 120
    compact_payload: dict[str, Any] = {
        "schema": payload.get("schema", "swarm_code_analysis/v1"),
        "root": payload.get("root", ""),
        "stats": payload.get("stats", {}),
        "note": payload.get("note", ""),
    }
    files = payload.get("files")
    if isinstance(files, list):
        compact_payload["files"] = files[:_MAX_STATE_FILES]
        if len(files) > _MAX_STATE_FILES:
            compact_payload["note"] = (
                f"Showing {_MAX_STATE_FILES} of {len(files)} files. "
                f"Full analysis: artifacts/{task_id}/code_analysis.json"
            )
    conventions = payload.get("conventions")
    if isinstance(conventions, dict):
        compact_payload["conventions"] = conventions
    rg = payload.get("relation_graph")
    if isinstance(rg, dict):
        nodes = rg.get("nodes") or []
        compact_payload["relation_graph"] = {
            "schema": rg.get("schema", ""),
            "nodes": nodes[:50],
            "edges": (rg.get("edges") or [])[:100],
        }

    wiki_parts = [summary]
    stats = compact_payload.get("stats") or {}
    if stats:
        wiki_parts.append(f"\n## Stats\n- Files scanned: {stats.get('scanned_files', '?')}")
        for k, v in stats.items():
            if k != "scanned_files" and v:
                wiki_parts.append(f"- {k}: {v}")
    conventions = compact_payload.get("conventions")
    if isinstance(conventions, dict) and conventions:
        wiki_parts.append("\n## Conventions")
        for k, v in list(conventions.items())[:20]:
            wiki_parts.append(f"- **{k}**: {v}")
    files = compact_payload.get("files")
    if isinstance(files, list) and files:
        wiki_parts.append(f"\n## Key files ({min(len(files), 30)} shown)")
        for f in files[:30]:
            path = f.get("path", "") if isinstance(f, dict) else str(f)
            wiki_parts.append(f"- `{path}`")
    wiki_content = "\n".join(wiki_parts)
    if wiki_content.strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "analyze_code", wiki_content)
    return {
        "code_analysis": compact_payload,
        "analyze_code_output": summary,
    }


def generate_documentation_node(state: PipelineState) -> dict[str, Any]:
    import backend.App.orchestration.application.routing.pipeline_graph as _pg
    _CodeDiagramAgent = _pg.CodeDiagramAgent
    _DocGenerateAgent = _pg.DocGenerateAgent
    _remote_api_kwargs = _pg._remote_api_client_kwargs

    agent_config = state.get("agent_config") or {}
    _ca_raw = state.get("code_analysis")
    code_analysis: dict[str, Any] = _ca_raw if isinstance(_ca_raw, dict) else {}
    compact = _compact_code_analysis_for_prompt(code_analysis)
    compact_doc_pass = _compact_code_analysis_for_prompt(
        code_analysis, max_chars=_doc_generate_second_pass_analysis_max_chars()
    )
    product_ctx = _documentation_product_context_block(
        state, log_node="generate_documentation_node"
    )
    product_section = (
        "[Product / specification context]\n"
        f"{product_ctx}\n\n"
        if product_ctx.strip()
        else ""
    )
    loc = _documentation_locale_line(state)
    langs = _swarm_languages_line(state)
    prefix = _swarm_prompt_prefix(state)

    diagram_cfg = agent_config.get("code_diagram") if isinstance(agent_config.get("code_diagram"), dict) else {}
    if not isinstance(diagram_cfg, dict):
        diagram_cfg = {}
    diagram_agent = _CodeDiagramAgent(
        system_prompt_path_override=diagram_cfg.get("prompt_path") or diagram_cfg.get("prompt"),
        model_override=diagram_cfg.get("model"),
        environment_override=diagram_cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, diagram_cfg),
        **_remote_api_kwargs(state),
    )
    diagram_prompt = Template(_prompt_fragment("diagram_prompt_template")).safe_substitute(
        prefix=prefix,
        locale=loc,
        languages=langs,
        product_section=product_section,
        compact=compact,
    )
    diagram_out = _run_agent_with_boundary(state, diagram_agent, diagram_prompt)

    _dg_raw = agent_config.get("doc_generate")
    doc_generate_cfg: dict[str, Any] = _dg_raw if isinstance(_dg_raw, dict) else {}
    doc_agent = _DocGenerateAgent(
        system_prompt_path_override=doc_generate_cfg.get("prompt_path") or doc_generate_cfg.get("prompt"),
        model_override=doc_generate_cfg.get("model"),
        environment_override=doc_generate_cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, doc_generate_cfg),
        **_remote_api_kwargs(state),
    )
    doc_prompt = Template(_prompt_fragment("doc_prompt_template")).safe_substitute(
        prefix=prefix,
        locale=loc,
        languages=langs,
        product_section=product_section,
        diagram_out=diagram_out,
        compact_doc_pass=compact_doc_pass,
    )
    doc_out = _run_agent_with_boundary(state, doc_agent, doc_prompt)
    merged = (
        "## Diagrams (Mermaid / structure)\n\n"
        f"{diagram_out}\n\n"
        "## Documentation (README / API / ARCHITECTURE)\n\n"
        f"{doc_out}"
    )
    if (diagram_out or "").strip():
        write_step_wiki(state, "code_diagram", diagram_out)
    if (merged or "").strip():
        write_step_wiki(state, "generate_documentation", merged)
    result: dict[str, Any] = {
        "code_diagram_output": diagram_out,
        "code_diagram_model": diagram_agent.used_model,
        "code_diagram_provider": diagram_agent.used_provider,
        "generate_documentation_output": merged,
        "generate_documentation_model": doc_agent.used_model,
        "generate_documentation_provider": doc_agent.used_provider,
    }
    return result


def problem_spotter_node(state: PipelineState) -> dict[str, Any]:
    agent_config = state.get("agent_config") or {}
    _ca_raw = state.get("code_analysis")
    code_analysis: dict[str, Any] = _ca_raw if isinstance(_ca_raw, dict) else {}
    analyze_code_output = (state.get("analyze_code_output") or "").strip()
    use_mcp = _should_use_mcp_for_workspace(state)
    if use_mcp:
        workspace_root = (state.get("workspace_root") or "").strip()
        compact = f"[Use MCP filesystem tools to explore the workspace: {workspace_root}]\n"
    else:
        compact = _compact_code_analysis_for_prompt(code_analysis)
    _ps_raw = agent_config.get("problem_spotter")
    problem_spotter_cfg: dict[str, Any] = _ps_raw if isinstance(_ps_raw, dict) else {}
    agent = ProblemSpotterAgent(
        system_prompt_path_override=problem_spotter_cfg.get("prompt_path") or problem_spotter_cfg.get("prompt"),
        model_override=problem_spotter_cfg.get("model"),
        environment_override=problem_spotter_cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, problem_spotter_cfg),
        **_remote_api_client_kwargs_for_role(state, problem_spotter_cfg),
    )
    spec_block = _effective_spec_block_for_doc_chain(
        state, log_node="problem_spotter_node"
    )
    if use_mcp:
        spec_hint = spec_block[:1500] + "\n…[full spec at .swarm/spec/spec.md]\n" if len(spec_block) > 1500 else spec_block
        spec_section = (
            "[Approved product context — tie findings to these goals and scope]\n"
            f"{spec_hint}\n\n"
            if spec_hint.strip()
            else ""
        )
        doc_draft_block = ""  # model reads documentation via MCP tools
        analyze_code_summary_block = (
            f"[Code analysis summary]\n{analyze_code_output}\n\n"
            if analyze_code_output
            else ""
        )
    else:
        spec_section = (
            "[Approved product context — tie findings to these goals and scope]\n"
            f"{spec_block}\n\n"
            if spec_block.strip()
            else ""
        )
        doc_draft = embedded_review_artifact(
            state,
            state.get("generate_documentation_output", ""),
            log_node="problem_spotter_node",
            part_name="generate_documentation_output",
            env_name="SWARM_DOC_CHAIN_GENERATED_DOC_MAX_CHARS",
            default_max=32_000,
        )
        if len(doc_draft) > 12_000:
            doc_draft = doc_draft[:12_000] + "\n…[truncated — SWARM_DOC_CHAIN_GENERATED_DOC_MAX_CHARS controls this]"
        doc_draft_block = "[Documentation / diagrams draft]\n" + doc_draft + "\n"
        analyze_code_summary_block = (
            f"[Code analysis summary]\n{analyze_code_output}\n\n"
            if analyze_code_output
            else ""
        )
    _wiki_block = ""
    workspace_root = (state.get("workspace_root") or "").strip()
    if workspace_root:
        try:
            from backend.App.workspace.application.wiki.wiki_context_loader import (
                load_wiki_context,
                query_for_pipeline_step,
            )
            _wiki_query = query_for_pipeline_step(state, "problem_spotter")
            _wiki = load_wiki_context(workspace_root, query=_wiki_query or None, max_chars=2000)
            if _wiki:
                _wiki_capped = _wiki[:2000] + "\n…[wiki truncated]" if len(_wiki) > 2000 else _wiki
                _wiki_block = f"[Project wiki — previous pipeline decisions]\n{_wiki_capped}\n\n"
        except Exception:
            pass

    prompt = Template(_prompt_fragment("problem_spotter_prompt_template")).safe_substitute(
        swarm_prefix=_swarm_prompt_prefix(state),
        locale=_documentation_locale_line(state),
        languages=_swarm_languages_line(state),
        wiki_block=_wiki_block,
        spec_section=spec_section,
        analyze_code_summary_block=analyze_code_summary_block,
        compact=compact,
        doc_draft_block=doc_draft_block,
    )
    agent_output, used_model, used_provider = _llm_agent_run_with_optional_mcp(agent, prompt, state, readonly_tools=True)
    agent_output, validated_repo_evidence = ensure_validated_repo_evidence(
        raw_output=agent_output,
        base_prompt=prompt,
        workspace_root=str(state.get("workspace_root") or ""),
        step_id="problem_spotter_node",
        retry_run=lambda retry_prompt: _llm_agent_run_with_optional_mcp(agent, retry_prompt, state, readonly_tools=True)[0],
    )
    if (agent_output or "").strip():
        _write_plan_artifact(state, "problem-spotter.md", agent_output)
        write_step_wiki(state, "problem_spotter", agent_output)
    return {
        "problem_spotter_output": agent_output,
        "problem_spotter_model": used_model,
        "problem_spotter_provider": used_provider,
        "problem_spotter_repo_evidence": validated_repo_evidence.get("repo_evidence") or [],
        "problem_spotter_unverified_claims": validated_repo_evidence.get("unverified_claims") or [],
    }


def refactor_plan_node(state: PipelineState) -> dict[str, Any]:
    agent_config = state.get("agent_config") or {}
    _ca_raw = state.get("code_analysis")
    code_analysis: dict[str, Any] = _ca_raw if isinstance(_ca_raw, dict) else {}
    analyze_code_output = (state.get("analyze_code_output") or "").strip()
    use_mcp = _should_use_mcp_for_workspace(state)
    if use_mcp:
        workspace_root = (state.get("workspace_root") or "").strip()
        compact = f"[Use MCP filesystem tools to explore the workspace: {workspace_root}]\n"
    else:
        compact = _compact_code_analysis_for_prompt(code_analysis, max_chars=8000)
    _rp_raw = agent_config.get("refactor_plan")
    refactor_plan_cfg: dict[str, Any] = _rp_raw if isinstance(_rp_raw, dict) else {}
    agent = RefactorPlanAgent(
        system_prompt_path_override=refactor_plan_cfg.get("prompt_path") or refactor_plan_cfg.get("prompt"),
        model_override=refactor_plan_cfg.get("model"),
        environment_override=refactor_plan_cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, refactor_plan_cfg),
        **_remote_api_client_kwargs_for_role(state, refactor_plan_cfg),
    )
    spec_block = _effective_spec_block_for_doc_chain(
        state, log_node="refactor_plan_node"
    )
    if use_mcp:
        spec_hint = spec_block[:1500] + "\n…[full spec at .swarm/spec/spec.md]\n" if len(spec_block) > 1500 else spec_block
        spec_section = (
            "[Approved product context — refactoring plan MUST extend this (priorities, modules, AC)]\n"
            f"{spec_hint}\n\n"
            if spec_hint.strip()
            else ""
        )
        probs_text = (state.get("problem_spotter_output") or "")[:2500]
        if len(state.get("problem_spotter_output") or "") > 2500:
            probs_text += "\n…[truncated — use MCP to read .swarm/spec/ for full context]"
        analyze_code_summary_block = (
            f"[Code analysis summary]\n{analyze_code_output}\n\n"
            if analyze_code_output
            else ""
        )
    else:
        spec_section = (
            "[Approved product context — refactoring plan MUST extend this (priorities, modules, AC)]\n"
            f"{spec_block}\n\n"
            if spec_block.strip()
            else ""
        )
        probs_text = embedded_review_artifact(
            state,
            state.get("problem_spotter_output", ""),
            log_node="refactor_plan_node",
            part_name="problem_spotter_output",
            env_name="SWARM_DOC_CHAIN_PROBLEM_SPOTTER_MAX_CHARS",
            default_max=40_000,
        )
        if len(probs_text) > 20_000:
            probs_text = probs_text[:20_000] + "\n…[truncated]"
        analyze_code_summary_block = (
            f"[Code analysis summary]\n{analyze_code_output}\n\n"
            if analyze_code_output
            else ""
        )
    prior_repo_evidence_block = format_repo_evidence_for_prompt(
        {
            "repo_evidence": list(state.get("problem_spotter_repo_evidence") or []),
            "unverified_claims": list(state.get("problem_spotter_unverified_claims") or []),
        }
    )
    prompt = Template(_prompt_fragment("refactor_plan_prompt_template")).safe_substitute(
        swarm_prefix=_swarm_prompt_prefix(state),
        locale=_documentation_locale_line(state),
        languages=_swarm_languages_line(state),
        spec_section=spec_section,
        analyze_code_summary_block=analyze_code_summary_block,
        problems=probs_text,
        prior_repo_evidence=prior_repo_evidence_block,
        compact=compact,
    )
    agent_output, used_model, used_provider = _llm_agent_run_with_optional_mcp(agent, prompt, state, readonly_tools=True)
    agent_output, validated_repo_evidence = ensure_validated_repo_evidence(
        raw_output=agent_output,
        base_prompt=prompt,
        workspace_root=str(state.get("workspace_root") or ""),
        step_id="refactor_plan_node",
        retry_run=lambda retry_prompt: _llm_agent_run_with_optional_mcp(agent, retry_prompt, state, readonly_tools=True)[0],
    )
    if (agent_output or "").strip():
        _write_plan_artifact(state, "refactor-plan.md", agent_output)
        write_step_wiki(state, "refactor_plan", agent_output)
    return {
        "refactor_plan_output": agent_output,
        "refactor_plan_model": used_model,
        "refactor_plan_provider": used_provider,
        "refactor_plan_repo_evidence": validated_repo_evidence.get("repo_evidence") or [],
        "refactor_plan_unverified_claims": validated_repo_evidence.get("unverified_claims") or [],
    }


def human_code_review_node(state: PipelineState) -> dict[str, Any]:
    bundle = (
        f"Documentation and diagrams:\n{state.get('generate_documentation_output', '')}\n\n"
        f"Problems:\n{state.get('problem_spotter_output', '')}\n\n"
        f"Refactoring plan:\n{state.get('refactor_plan_output', '')}"
    )
    agent = _make_human_agent(state, "code_review")
    return {"code_review_human_output": agent.run(bundle)}

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState

logger = logging.getLogger(__name__)


def _context_budget_for_step(
    step_id: str,
    agent_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    from backend.App.orchestration.application.context.context_budget import (
        context_budget_as_dict,
        get_context_budget,
    )

    return context_budget_as_dict(get_context_budget(step_id, agent_config))


def render_pipeline_context_block(state: PipelineState, current_step_id: str) -> str:
    from backend.App.orchestration.application.routing.pipeline_graph import PIPELINE_STEP_REGISTRY

    agent_config = state.get("agent_config") if isinstance(state, dict) else None
    budget = _context_budget_for_step(current_step_id, agent_config)
    raw_step_ids: Any = state.get("_pipeline_step_ids")
    step_ids: list[str] = list(raw_step_ids) if isinstance(raw_step_ids, list) else []

    try:
        idx = step_ids.index(current_step_id)
    except ValueError:
        idx = -1

    def _label(step_id: str) -> str:
        row = PIPELINE_STEP_REGISTRY.get(step_id)
        return row[0] if row else step_id

    def _content_steps(ids: list[str]) -> list[str]:
        return [
            entry for entry in ids
            if entry in PIPELINE_STEP_REGISTRY
            and not entry.startswith(("review_", "human_"))
        ]

    lines: list[str] = ["[Pipeline context]"]

    if step_ids and idx >= 0:
        prev_content = _content_steps(step_ids[:idx])
        next_content = _content_steps(step_ids[idx + 1:])
        if prev_content:
            lines.append("Completed: " + " → ".join(prev_content))
        lines.append(f"Current step: {current_step_id} — {_label(current_step_id)}")
        if next_content:
            lines.append("Next steps: " + " → ".join(next_content[:4]))
    else:
        lines.append(f"Current step: {current_step_id} — {_label(current_step_id)}")

    if budget.get("include_summaries", True):
        _has_merged_spec = bool((state.get("spec_output") or "").strip())
        _skip_if_spec = (
            {"pm_output", "ba_output", "arch_output"} if _has_merged_spec else set()
        )
        summaries: list[str] = []
        for key, label in (
            ("clarify_input_human_output", "UserClarification"),
            ("source_research_output", "SourceResearch"),
            ("pm_output", "PM"),
            ("ba_output", "BA"),
            ("arch_output", "Architect"),
            ("spec_output", "Spec"),
            ("devops_output", "DevOps"),
            ("dev_output", "Dev"),
        ):
            if key in _skip_if_spec:
                continue
            val = str(state.get(key) or "").strip()
            if val and key != f"{current_step_id}_output":
                summaries.append(f"  {label}: {val[:300].replace(chr(10), ' ')}…")
        if summaries:
            lines.append("Previous agents summary:")
            lines.extend(summaries)

    workspace_root = (state.get("workspace_root") or "").strip()
    if workspace_root:
        try:
            from backend.App.workspace.application.wiki.wiki_context_loader import (
                load_wiki_context,
                query_for_pipeline_step,
            )
            wiki_query = query_for_pipeline_step(state, current_step_id)
            fresh_wiki = load_wiki_context(workspace_root, query=wiki_query or None)
            if fresh_wiki:
                state["wiki_context"] = fresh_wiki
        except Exception:
            pass

    _wiki_chars = int(budget.get("wiki_chars", 6000) or 0)
    wiki_ctx = (state.get("wiki_context") or "").strip()
    if wiki_ctx and _wiki_chars > 0:
        lines.append("\n[Project wiki memory]")
        wiki_ctx = _trim_wiki_context(
            wiki_ctx,
            wiki_query=wiki_query if workspace_root else "",
            wiki_chars=_wiki_chars,
        )
        try:
            from backend.App.orchestration.application.enforcement.untrusted_content import wrap_untrusted
            wiki_ctx = wrap_untrusted(wiki_ctx, source="project_wiki")
        except Exception:
            pass
        lines.append(wiki_ctx)

    return "\n".join(lines) + "\n\n"


def _trim_wiki_context(text: str, *, wiki_query: str, wiki_chars: int) -> str:
    try:
        from backend.App.orchestration.application.context.smart_context_builder import (
            build_context,
            smart_context_enabled,
        )
        if smart_context_enabled() and wiki_query:
            return build_context(
                [("Wiki", text)],
                query=wiki_query,
                budget_chars=wiki_chars,
            )
        if len(text) > wiki_chars:
            return text[:wiki_chars] + "\n…[wiki truncated]"
        return text
    except Exception:
        if len(text) > wiki_chars:
            return text[:wiki_chars] + "\n…[wiki truncated]"
        return text


def render_project_knowledge_block(
    state: PipelineState,
    *,
    max_chars: int = 2500,
    step_id: Optional[str] = None,
) -> str:
    brief = str(state.get("workspace_evidence_brief") or "").strip()
    if not brief:
        return ""
    if step_id:
        agent_config = state.get("agent_config") if isinstance(state, dict) else None
        budget = _context_budget_for_step(step_id, agent_config)
        knowledge_chars = int(budget.get("knowledge_chars", max_chars) or 0)
        if knowledge_chars <= 0:
            return ""
        max_chars = min(max_chars, knowledge_chars)
    if len(brief) > max_chars:
        brief = brief[:max_chars] + "\n…[workspace brief truncated]"
    try:
        from backend.App.orchestration.application.enforcement.untrusted_content import wrap_untrusted
        brief = wrap_untrusted(brief, source="workspace_evidence")
    except Exception:
        pass
    return (
        "[Project knowledge — workspace structure and documentation]\n"
        + brief
        + "\n\n"
    )


def render_dev_sibling_tasks_block(
    all_tasks: list[dict],
    current_index: int,
) -> str:
    if len(all_tasks) <= 1:
        return ""
    lines: list[str] = ["[File ownership across all subtasks — avoid conflicts]"]
    for offset, task_entry in enumerate(all_tasks):
        task_id = str(task_entry.get("id") or offset + 1)
        title = str(task_entry.get("title") or f"T{offset + 1}")[:60]
        paths = [
            str(path)
            for path in (task_entry.get("expected_paths") or [])
            if str(path).strip()
        ]
        marker = " ← THIS SUBTASK" if offset == current_index else ""
        if paths:
            lines.append(f"  [{task_id}] {title}{marker}: {', '.join(paths[:6])}")
        else:
            lines.append(f"  [{task_id}] {title}{marker}: (no declared paths)")
    lines.append(
        "RULE: do NOT write to files listed under other subtasks above unless "
        "your scope explicitly requires it."
    )
    return "\n".join(lines) + "\n\n"


__all__ = (
    "render_pipeline_context_block",
    "render_project_knowledge_block",
    "render_dev_sibling_tasks_block",
)

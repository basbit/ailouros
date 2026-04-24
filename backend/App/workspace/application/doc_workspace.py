"""Write pipeline documentation to workspace (no LangGraph dependency)."""

from __future__ import annotations

import logging
from pathlib import Path

from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState

logger = logging.getLogger(__name__)


# Wiki article config per pipeline step: (subfolder, slug, title, tag, max_chars, links_to)
# links_to: list of "subfolder/slug" node IDs this article depends on (for graph edges)
_WIKI_STEP_CONFIG: dict[str, tuple[str, str, str, str, int, list[str]]] = {
    # Planning phase
    "pm":              ("planning",      "pm-output",        "Project Management Plan",  "planning",      80_000, []),
    "ba":              ("planning",      "ba-output",        "Business Analysis",        "planning",      80_000, ["planning/pm-output"]),
    "ba_arch_debate":  ("planning",      "ba-arch-debate",   "BA ↔ Arch Debate",         "planning",      60_000, ["planning/ba-output", "architecture/arch-output"]),
    "spec_merge":      ("planning",      "spec-merge",       "Specification (merged)",   "planning",      80_000, ["planning/ba-output", "architecture/arch-output"]),
    # Architecture
    "architect":       ("architecture",  "arch-output",      "Architecture Decisions",   "architecture",  80_000, ["planning/ba-output", "planning/pm-output"]),
    "code_quality_architect": ("architecture", "code-quality", "Code Quality Architecture", "architecture", 60_000, ["architecture/arch-output", "planning/spec-merge"]),
    # Analysis & documentation
    "analyze_code":    ("analysis",      "code-analysis",    "Code Analysis",            "analysis",      60_000, ["architecture/arch-output"]),
    "code_diagram":    ("documentation", "diagrams",    "Mermaid Diagrams",         "documentation", 60_000, ["analysis/code-analysis"]),
    "generate_documentation": ("documentation", "docs",      "Generated Documentation",  "documentation", 60_000, ["analysis/code-analysis", "documentation/diagrams"]),
    "problem_spotter": ("analysis",      "problem-spotter",  "Problem Spotter Report",   "analysis",      60_000, ["analysis/code-analysis"]),
    "refactor_plan":   ("analysis",      "refactor-plan",    "Refactor Plan",            "analysis",      60_000, ["analysis/problem-spotter"]),
    # Dev lead & development
    "dev_lead":        ("development",   "dev-lead-plan",    "Dev Lead Plan",            "development",   60_000, ["architecture/arch-output", "planning/ba-output"]),
    "dev":             ("development",   "dev-output",       "Development Output",       "development",   20_000, ["development/dev-lead-plan", "architecture/arch-output"]),
    # QA & DevOps
    "qa":              ("qa",            "qa-report",        "QA Report",                "qa",            60_000, ["development/dev-output"]),
    "devops":          ("devops",        "deployment",       "DevOps Notes",             "devops",        60_000, ["architecture/arch-output"]),
    # Design phase
    "ux_researcher":   ("design",        "ux-research",      "UX Research",              "design",        60_000, ["planning/spec-merge"]),
    "ux_architect":    ("design",        "ux-architecture",  "UX Architecture",          "design",        60_000, ["design/ux-research", "planning/spec-merge"]),
    "ui_designer":     ("design",        "ui-design",        "UI Design",                "design",        60_000, ["design/ux-architecture", "design/ux-research"]),
    "image_generator": ("media",         "image-generation", "Image Generation",         "media",         60_000, ["design/ui-design"]),
    "audio_generator": ("media",         "audio-generation", "Audio Generation",         "media",         60_000, ["planning/spec-merge"]),
    # Marketing phase
    "seo_specialist":  ("marketing",     "seo-strategy",     "SEO Strategy",             "marketing",     60_000, ["development/dev-output", "qa/qa-report"]),
    "ai_citation_strategist": ("marketing", "ai-citation",   "AI Citation Strategy",     "marketing",     60_000, ["marketing/seo-strategy"]),
    "app_store_optimizer":    ("marketing", "app-store-aso",  "App Store Optimization",   "marketing",     60_000, ["marketing/seo-strategy"]),
}


def write_step_wiki(
    state: "PipelineState",
    step_name: str,
    content: str,
) -> None:
    """Write a pipeline step output as a wiki article to ``.swarm/wiki/`` in the workspace.

    Does NOT require ``workspace_apply_writes`` or ``SWARM_ALLOW_WORKSPACE_WRITE`` —
    the wiki is internal swarm metadata (.swarm/wiki/), not project source files.
    Silently skips when workspace_root is empty or content is blank.

    After writing the article, the graph.json will be rebuilt lazily on the next
    GET /api/wiki/graph request (wiki_service.get_or_build_graph detects mtime changes).
    """
    workspace_root = (state.get("workspace_root") or "").strip()
    if not workspace_root or not (content or "").strip():
        return

    cfg = _WIKI_STEP_CONFIG.get(step_name)
    if cfg is None:
        # Fallback for unknown step names
        _links: list[str] = []
        subfolder, slug, title, tag, max_chars, links = step_name, step_name, step_name.title(), step_name, 60_000, _links
    else:
        subfolder, slug, title, tag, max_chars, links = cfg

    body = content if len(content) <= max_chars else content[:max_chars] + "\n\n…(truncated)"

    # Build frontmatter with optional links for graph edges
    fm_lines = [f"title: {title}", f"tags: [{tag}]"]
    if links:
        fm_lines.append(f"links: [{', '.join(links)}]")

    try:
        wiki_dir = Path(workspace_root).resolve() / ".swarm" / "wiki" / subfolder
        wiki_dir.mkdir(parents=True, exist_ok=True)
        article = wiki_dir / f"{slug}.md"
        frontmatter = "\n".join(fm_lines)
        article.write_text(
            f"---\n{frontmatter}\n---\n\n# {title}\n\n{body}\n",
            encoding="utf-8",
        )
        logger.info(
            "wiki step %r → .swarm/wiki/%s/%s.md (%d chars) task_id=%s",
            step_name, subfolder, slug, len(body),
            (state.get("task_id") or "")[:36],
        )
        # Eagerly rebuild graph.json so the frontend graph view reflects the
        # latest wiki state without waiting for a manual GET /api/wiki/graph.
        try:
            from backend.App.workspace.application.wiki_service import get_or_build_graph
            wiki_root = Path(workspace_root).resolve() / ".swarm" / "wiki"
            get_or_build_graph(wiki_root)
        except Exception as _graph_exc:  # noqa: BLE001  # graph refresh is non-critical display enhancement; article write already succeeded above
            logger.warning("write_step_wiki: graph rebuild failed: %s", _graph_exc)
    except OSError as exc:
        logger.warning("write_step_wiki: failed to write wiki for step %r: %s", step_name, exc)

"""Write pipeline documentation to workspace (no LangGraph dependency)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Mapping

from backend.App.orchestration.application.pipeline_state import PipelineState

logger = logging.getLogger(__name__)


def _swarm_block(state: Mapping[str, Any]) -> dict[str, Any]:
    ac = state.get("agent_config") or {}
    sw = ac.get("swarm")
    return sw if isinstance(sw, dict) else {}


def write_generated_documentation_to_workspace(  # DEPRECATED: use write_step_wiki instead
    state: PipelineState,
    merged: str,
    diagram_out: str,
) -> list[str]:
    """Write ``generate_documentation`` step output to the project directory.

    Conditions: non-empty ``workspace_root``, ``workspace_apply_writes``, ``SWARM_ALLOW_WORKSPACE_WRITE``.
    Disable: ``agent_config.swarm.write_documentation_to_workspace: false``.

    Paths (relative to root): directory ``swarm.documentation_workspace_dir`` or env
    ``SWARM_DOCS_WORKSPACE_DIR`` (default ``docs/swarm``); files
    ``documentation_workspace_filename`` (default ``AGENT_SWARM_DOCS.md``) and
    ``documentation_diagram_filename`` (default ``DIAGRAMS.md``).

    Returns a list of relative posix paths of written files.
    """
    written: list[str] = []
    wr = (state.get("workspace_root") or "").strip()
    if not wr or not state.get("workspace_apply_writes"):
        return written
    try:
        from backend.App.workspace.infrastructure.workspace_io import workspace_write_allowed
        from backend.App.workspace.infrastructure.patch_parser import safe_relative_path
    except ImportError:
        logger.warning("write_generated_documentation_to_workspace: workspace_io unavailable")
        return written
    if not workspace_write_allowed():
        logger.warning(
            "documentation not written to project: set SWARM_ALLOW_WORKSPACE_WRITE=1 (task_id=%s)",
            (state.get("task_id") or "")[:36],
        )
        return written
    sw = _swarm_block(state)
    if sw.get("write_documentation_to_workspace") is False:
        return written
    subdir_raw = (
        sw.get("documentation_workspace_dir")
        or os.getenv("SWARM_DOCS_WORKSPACE_DIR", "")
        or "docs/swarm"
    )
    subdir = str(subdir_raw).strip().replace("\\", "/").strip("/")
    if not subdir or ".." in subdir or subdir.startswith(".."):
        subdir = "docs/swarm"
    main_name = str(sw.get("documentation_workspace_filename") or "AGENT_SWARM_DOCS.md").strip()
    if not main_name or ".." in main_name or main_name.startswith("/"):
        main_name = "AGENT_SWARM_DOCS.md"
    diagram_name = str(sw.get("documentation_diagram_filename") or "DIAGRAMS.md").strip()
    if not diagram_name or ".." in diagram_name or diagram_name.startswith("/"):
        diagram_name = "DIAGRAMS.md"

    root = Path(wr).resolve()
    pairs: list[tuple[str, str]] = []
    if (merged or "").strip():
        pairs.append((f"{subdir}/{main_name}", merged))
    if (diagram_out or "").strip():
        pairs.append((f"{subdir}/{diagram_name}", diagram_out))

    for rel, body in pairs:
        rel_posix = rel.replace("\\", "/")
        try:
            from backend.App.workspace.infrastructure.workspace_io import _assert_under_workspace
            dest = safe_relative_path(root, rel_posix)
            _assert_under_workspace(dest, root)
        except ValueError as e:
            logger.warning("documentation: unsafe path %r: %s", rel_posix, e)
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding="utf-8")
            written.append(dest.relative_to(root).as_posix())
            logger.info(
                "documentation written to workspace: %s (%d chars) task_id=%s",
                dest.relative_to(root).as_posix(),
                len(body),
                (state.get("task_id") or "")[:36],
            )
        except OSError as e:
            logger.warning("documentation: failed to write %s: %s", rel_posix, e)
    return written


_PIPELINE_STEP_FILENAMES: dict[str, str] = {
    "pm": "pm_output.md",
    "ba": "ba_output.md",
    "architect": "arch_output.md",
    "spec": "spec.md",
}

# Wiki article config per pipeline step: (subfolder, slug, title, tag, max_chars)
# (subfolder, slug, title, tag, max_chars, links_to)
# links_to: list of "subfolder/slug" node IDs this article depends on (for graph edges)
_WIKI_STEP_CONFIG: dict[str, tuple[str, str, str, str, int, list[str]]] = {
    # Planning phase
    "pm":              ("planning",      "pm-output",        "Project Management Plan",  "planning",      80_000, []),
    "ba":              ("planning",      "ba-output",        "Business Analysis",        "planning",      80_000, ["planning/pm-output"]),
    "ba_arch_debate":  ("planning",      "ba-arch-debate",   "BA ↔ Arch Debate",         "planning",      60_000, ["planning/ba-output", "architecture/arch-output"]),
    "spec_merge":      ("planning",      "spec-merge",       "Specification (merged)",   "planning",      80_000, ["planning/ba-output", "architecture/arch-output"]),
    # Architecture
    "architect":       ("architecture",  "arch-output",      "Architecture Decisions",   "architecture",  80_000, ["planning/ba-output", "planning/pm-output"]),
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
    # Marketing phase
    "seo_specialist":  ("marketing",     "seo-strategy",     "SEO Strategy",             "marketing",     60_000, ["development/dev-output", "qa/qa-report"]),
    "ai_citation_strategist": ("marketing", "ai-citation",   "AI Citation Strategy",     "marketing",     60_000, ["marketing/seo-strategy"]),
    "app_store_optimizer":    ("marketing", "app-store-aso",  "App Store Optimization",   "marketing",     60_000, ["marketing/seo-strategy"]),
}

# Filenames for doc-chain steps written to docs/swarm/ (same dir as generated documentation).
_DOC_CHAIN_STEP_FILENAMES: dict[str, str] = {
    "refactor_plan": "refactor-plan.md",
    "problem_spotter": "problem-spotter.md",
}


def write_pipeline_step_to_workspace(  # DEPRECATED: use write_step_wiki instead
    state: "PipelineState",
    step_name: str,
    content: str,
) -> "str | None":
    """Write a planning-step output to .swarm/spec/<step>.md in the workspace.

    Conditions: non-empty workspace_root, workspace_apply_writes=True,
    SWARM_ALLOW_WORKSPACE_WRITE=1. Skip silently when any condition is missing.
    Disable explicitly via agent_config.swarm.write_pipeline_steps_to_workspace: false.

    Returns the relative posix path of the written file, or None.
    """
    workspace_root = (state.get("workspace_root") or "").strip()
    if not workspace_root or not state.get("workspace_apply_writes"):
        return None
    if not (content or "").strip():
        return None
    try:
        from backend.App.workspace.infrastructure.workspace_io import workspace_write_allowed
        from backend.App.workspace.infrastructure.patch_parser import safe_relative_path
        from backend.App.workspace.infrastructure.workspace_io import _assert_under_workspace
    except ImportError:
        logger.warning("write_pipeline_step_to_workspace: workspace_io unavailable")
        return None
    if not workspace_write_allowed():
        return None
    swarm_config = _swarm_block(state)
    if swarm_config.get("write_pipeline_steps_to_workspace") is False:
        return None
    filename = _PIPELINE_STEP_FILENAMES.get(step_name, f"{step_name}.md")
    rel_posix = f".swarm/spec/{filename}"
    root = Path(workspace_root).resolve()
    try:
        dest = safe_relative_path(root, rel_posix)
        _assert_under_workspace(dest, root)
    except ValueError as path_error:
        logger.warning("write_pipeline_step_to_workspace: unsafe path %r: %s", rel_posix, path_error)
        return None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        relative_path = dest.relative_to(root).as_posix()
        logger.info(
            "pipeline step %r written to workspace: %s (%d chars) task_id=%s",
            step_name,
            relative_path,
            len(content),
            (state.get("task_id") or "")[:36],
        )
        return relative_path
    except OSError as write_error:
        logger.warning("write_pipeline_step_to_workspace: failed to write %s: %s", rel_posix, write_error)
        return None


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
        subfolder, slug, title, tag, max_chars, links = step_name, step_name, step_name.title(), step_name, 60_000, []
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
    except OSError as exc:
        logger.warning("write_step_wiki: failed to write wiki for step %r: %s", step_name, exc)


def write_doc_chain_step_to_workspace(  # DEPRECATED: use write_step_wiki instead
    state: "PipelineState",
    step_name: str,
    content: str,
) -> "str | None":
    """Write a doc-chain step output (problem_spotter, refactor_plan) to docs/swarm/ in the workspace.

    Uses the same directory as generated documentation (docs/swarm/ or SWARM_DOCS_WORKSPACE_DIR).
    Conditions: non-empty workspace_root, workspace_apply_writes=True,
    SWARM_ALLOW_WORKSPACE_WRITE=1. Skip silently when any condition is missing.

    Returns the relative posix path of the written file, or None.
    """
    workspace_root = (state.get("workspace_root") or "").strip()
    if not workspace_root or not state.get("workspace_apply_writes"):
        return None
    if not (content or "").strip():
        return None
    try:
        from backend.App.workspace.infrastructure.workspace_io import workspace_write_allowed
        from backend.App.workspace.infrastructure.patch_parser import safe_relative_path
        from backend.App.workspace.infrastructure.workspace_io import _assert_under_workspace
    except ImportError:
        logger.warning("write_doc_chain_step_to_workspace: workspace_io unavailable")
        return None
    if not workspace_write_allowed():
        return None
    swarm_config = _swarm_block(state)
    subdir_raw = (
        swarm_config.get("documentation_workspace_dir")
        or os.getenv("SWARM_DOCS_WORKSPACE_DIR", "")
        or "docs/swarm"
    )
    subdir = str(subdir_raw).strip().replace("\\", "/").strip("/")
    if not subdir or ".." in subdir or subdir.startswith(".."):
        subdir = "docs/swarm"
    filename = _DOC_CHAIN_STEP_FILENAMES.get(step_name, f"{step_name}.md")
    rel_posix = f"{subdir}/{filename}"
    root = Path(workspace_root).resolve()
    try:
        dest = safe_relative_path(root, rel_posix)
        _assert_under_workspace(dest, root)
    except ValueError as path_error:
        logger.warning("write_doc_chain_step_to_workspace: unsafe path %r: %s", rel_posix, path_error)
        return None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        relative_path = dest.relative_to(root).as_posix()
        logger.info(
            "doc-chain step %r written to workspace: %s (%d chars) task_id=%s",
            step_name,
            relative_path,
            len(content),
            (state.get("task_id") or "")[:36],
        )
        return relative_path
    except OSError as write_error:
        logger.warning("write_doc_chain_step_to_workspace: failed to write %s: %s", rel_posix, write_error)
        return None

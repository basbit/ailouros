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


def write_generated_documentation_to_workspace(
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

# Filenames for doc-chain steps written to docs/swarm/ (same dir as generated documentation).
_DOC_CHAIN_STEP_FILENAMES: dict[str, str] = {
    "refactor_plan": "refactor-plan.md",
    "problem_spotter": "problem-spotter.md",
}


def write_pipeline_step_to_workspace(
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


def write_doc_chain_step_to_workspace(
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

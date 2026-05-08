from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional


def apply_final_workspace_writes(
    task_id: str,
    pipeline_snapshot: dict[str, Any],
    workspace_path: Optional[Path],
    workspace_apply_writes: bool,
    task_store: Any,
    cancel_event: Optional[threading.Event] = None,
    skip_all_shell: bool = False,
) -> None:
    from backend.App.workspace.infrastructure.workspace_io import workspace_write_allowed
    from backend.App.workspace.infrastructure.patch_parser import apply_from_devops_and_dev_outputs
    from backend.App.orchestration.infrastructure.shell_approval import run_shell_after_user_approval

    if not (workspace_path and workspace_apply_writes and workspace_write_allowed()):
        return

    run_sh = run_shell_after_user_approval(
        task_id,
        pipeline_snapshot,
        task_store,
        cancel_event=cancel_event,
        skip_all_shell=skip_all_shell,
    )
    pipeline_snapshot["workspace_writes"] = apply_from_devops_and_dev_outputs(
        pipeline_snapshot,
        workspace_path,
        run_shell=run_sh,
    )


def capture_workspace_diff_after_writes(
    pipeline_snapshot: dict[str, Any],
    workspace_path: Path,
) -> None:
    from backend.App.workspace.infrastructure.workspace_diff import capture_workspace_diff

    ws = pipeline_snapshot.get("workspace_writes") or {}
    all_changed = sorted(set(
        list(ws.get("written") or [])
        + list(ws.get("patched") or [])
        + list(ws.get("udiff_applied") or [])
    ))
    pipeline_snapshot["dev_workspace_diff"] = capture_workspace_diff(workspace_path, all_changed)


def workspace_followup_lines(
    workspace_path: Optional[Path],
    workspace_apply_writes: bool,
    pipeline_snapshot: dict[str, Any],
) -> list[str]:
    from backend.App.workspace.infrastructure.workspace_io import workspace_write_allowed
    from backend.App.workspace.infrastructure.patch_parser import any_snapshot_output_has_swarm

    lines: list[str] = []
    if not workspace_path:
        lines.append(
            "[orchestrator] workspace writes: skipped (workspace_root not set in request)\n"
        )
        return lines
    if not workspace_apply_writes:
        lines.append(
            "[orchestrator] workspace writes: skipped "
            "(workspace_write=false — enable checkbox in UI)\n"
        )
        return lines
    if not workspace_write_allowed():
        import os as _os

        if _os.getenv("AILOUROS_DESKTOP", "").strip() == "1":
            hint = (
                "(desktop runtime should set this automatically; "
                "restart the app if the capability is missing)"
            )
        else:
            hint = "(set SWARM_ALLOW_WORKSPACE_WRITE=1 on orchestrator)"
        lines.append(f"[orchestrator] workspace writes: skipped {hint}\n")
        return lines

    w = pipeline_snapshot.get("workspace_writes") or {}
    written = w.get("written") or []
    errs = w.get("errors") or []
    note = str(w.get("note") or "")
    lines.append(
        f"[orchestrator] workspace writes: files_written={len(written)} "
        f"errors={len(errs)} note={note!r}\n"
    )
    if errs:
        lines.append(f"[orchestrator] workspace write errors: {errs[:5]}\n")

    doc_paths = pipeline_snapshot.get("documentation_workspace_files")
    if isinstance(doc_paths, list) and doc_paths:
        lines.append(
            f"[orchestrator] generate_documentation → workspace files: {doc_paths}\n"
        )

    if not any_snapshot_output_has_swarm(pipeline_snapshot) and not (
        isinstance(doc_paths, list) and doc_paths
    ):
        lines.append(
            "[orchestrator] hint: no <swarm_file>/<swarm_patch>/<swarm_shell>/"
            "<swarm-command>/<swarm_udiff> "
            "in any *_output (or dev_task_outputs/qa_task_outputs) — "
            "models must emit those tags for workspace writes; plain markdown only goes to "
            "artifacts (generate_documentation при workspace_write дополнительно пишет "
            "docs/swarm/*.md — см. README)\n"
        )
    return lines

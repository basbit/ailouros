"""PipelineSSEHandler: convert pipeline events into SSE strings.

Extracted from stream_handlers.py.  Handles the inner loop that iterates
events from ``run_pipeline_stream`` and writes SSE data lines, agent artifacts,
pipeline snapshot fields, and task-store updates.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any, Optional

from backend.App.tasks.infrastructure.task_run_log import append_task_run_log
from backend.App.workspace.infrastructure.workspace_io import (
    _shell_allowlist,
    command_exec_allowed,
    extend_runtime_shell_allowlist,
    extract_command_binary,
    workspace_write_allowed,
)
from backend.App.workspace.infrastructure.patch_parser import (
    apply_workspace_pipeline,
    extract_shell_commands,
)
from backend.App.orchestration.domain.exceptions import (
    HumanApprovalRequired,
    HumanGateTimeout,
    PipelineCancelled,
)
from backend.UI.REST.presentation.sse import (
    _build_agent_sse_event,
    _ensure_task_dirs,
    _sse_delta_line,
)
from backend.UI.REST.presentation.sse_serializer import build_done, build_error
from backend.UI.REST.presentation.stream_utils import (
    _stream_finalise,
    _write_agent_artifact,
    _write_agents_error_txt,
)
from backend.UI.REST.utils import _pipeline_snapshot_for_disk, _stream_incremental_workspace_enabled

logger = logging.getLogger(__name__)

# Roles that are allowed to write files to workspace during pipeline (§9.1.4)
_WORKSPACE_WRITE_ROLES = frozenset(
    os.getenv("SWARM_WORKSPACE_WRITE_ROLES", "dev,devops").split(",")
)


class PipelineSSEHandler:
    """Iterate pipeline events and produce SSE string chunks.

    Constructor:
        serializer: Callable that builds SSE agent event lines (currently
            the module-level ``_build_agent_sse_event``).
        task_store: Task store instance (duck-typed).
        artifact_writer: Callable ``(agents_dir, agent, text)`` to persist
            per-agent text artifacts (currently ``_write_agent_artifact``).

    Usage::

        handler = PipelineSSEHandler(
            task_store=task_store,
            artifact_writer=_write_agent_artifact,
        )
        yield from handler.handle_events(
            events_gen=run_pipeline_stream(...),
            task_id=task_id,
            task_dir=task_dir,
            agents_dir=agents_dir,
            pipeline_snapshot=pipeline_snapshot,
            now=now,
            request_model=request_model,
            workspace_path=workspace_path,
            workspace_apply_writes=workspace_apply_writes,
            cancel_event=cancel_event,
        )
    """

    def __init__(
        self,
        task_store: Any,
        artifact_writer: Any = None,
    ) -> None:
        self._task_store = task_store
        self._artifact_writer = artifact_writer or _write_agent_artifact

    def handle_events(
        self,
        events_gen: Any,
        task_id: str,
        task_dir: Path,
        agents_dir: Path,
        pipeline_snapshot: dict[str, Any],
        now: int,
        request_model: str,
        workspace_path: Optional[Path] = None,
        workspace_apply_writes: bool = False,
        cancel_event: Optional[threading.Event] = None,
    ) -> Generator[str, None, None]:
        """Iterate *events_gen* and yield SSE strings, handling errors and finalisation.

        Args:
            events_gen: Iterable of pipeline event dicts from ``run_pipeline_stream``.
            task_id: Running task identifier.
            task_dir: Root directory for task artefacts.
            agents_dir: Directory for per-agent text files.
            pipeline_snapshot: Mutable snapshot dict that is updated as events arrive.
            now: Unix timestamp for SSE payloads.
            request_model: Model name used in SSE payloads.
            workspace_path: Resolved workspace path (or ``None``).
            workspace_apply_writes: Whether to apply file writes to the workspace.
            cancel_event: Threading event that signals pipeline cancellation.

        Yields:
            SSE ``data: ...\\n\\n`` strings.
        """
        from backend.App.orchestration.infrastructure.shell_approval import request_shell_approval
        from backend.App.workspace.infrastructure.workspace_io import (
            scoped_runtime_shell_allowlist,
        )

        # Enter a per-task runtime-allowlist scope. Anything the user approves
        # during this run (via request_shell_approval) is appended to this
        # scope and discarded at end-of-task — so a later task running in
        # the same worker thread cannot reuse the previous task's allowlist.
        with scoped_runtime_shell_allowlist():
            yield from self._handle_events_impl(
                events_gen=events_gen,
                task_id=task_id,
                task_dir=task_dir,
                agents_dir=agents_dir,
                pipeline_snapshot=pipeline_snapshot,
                now=now,
                request_model=request_model,
                workspace_path=workspace_path,
                workspace_apply_writes=workspace_apply_writes,
                cancel_event=cancel_event,
                request_shell_approval=request_shell_approval,
            )

    def _handle_events_impl(
        self,
        *,
        events_gen: Any,
        task_id: str,
        task_dir: Path,
        agents_dir: Path,
        pipeline_snapshot: dict[str, Any],
        now: int,
        request_model: str,
        workspace_path: Optional[Path],
        workspace_apply_writes: bool,
        cancel_event: Optional[threading.Event],
        request_shell_approval: Any,
    ) -> Generator[str, None, None]:
        try:
            for event in events_gen:
                if "agent" not in event:
                    # Meta-event without an agent (e.g. ``active_steps`` from
                    # ``run_pipeline_stream_staged`` when entering a parallel
                    # stage). Forward it as a plain delta line so the frontend
                    # still sees the announcement, then continue — there is no
                    # per-agent artifact/snapshot work to do for these.
                    msg_ev = str(event.get("message") or "")
                    if msg_ev:
                        meta_line = f"[orchestrator] {msg_ev}\n"
                        append_task_run_log(task_dir, meta_line.strip())
                        yield _sse_delta_line(now, request_model, meta_line)
                    continue
                agent = event["agent"]
                st_ev = event.get("status") or ""
                msg_ev = str(event.get("message") or "")
                append_task_run_log(task_dir, f"{agent} {st_ev}: {msg_ev}")
                self._task_store.update_task(
                    task_id,
                    status="in_progress",
                    agent=agent,
                    message=msg_ev,
                )

                if event.get("status") == "completed":
                    self._artifact_writer(agents_dir, agent, msg_ev)
                    if "model" in event:
                        pipeline_snapshot[f"{agent}_model"] = event.get("model", "")
                    if "provider" in event:
                        pipeline_snapshot[f"{agent}_provider"] = event.get("provider", "")
                    pipeline_snapshot[f"{agent}_output"] = msg_ev

                    if (
                        _stream_incremental_workspace_enabled()
                        and workspace_path
                        and workspace_apply_writes
                        and workspace_write_allowed()
                        and agent in _WORKSPACE_WRITE_ROLES
                        and msg_ev.strip()
                    ):
                        run_shell_flag = False
                        if command_exec_allowed():
                            shell_cmds = extract_shell_commands(msg_ev)
                            # Strip sudo commands up-front — they're structurally
                            # unsupported (no TTY, no password prompt) and would
                            # hang until the 5-min hard timeout. See §24 in
                            # docs/future-plan.md for the planned password-UI.
                            # We don't silently drop them: a dedicated status line
                            # is emitted so the user (and the dev agent on retry)
                            # know why nothing happened.
                            sudo_cmds = [
                                c for c in shell_cmds
                                if (extract_command_binary(c) or "") == "sudo"
                            ]
                            if sudo_cmds:
                                # Remove sudo from the automated batch — it's
                                # structurally unsupported (no TTY / no password
                                # prompt; hangs for 5 min until hard timeout).
                                # Route into the manual-execution dialog instead:
                                # the user runs the command in their own terminal
                                # and clicks Done (or Cancel) in the UI. See
                                # docs/future-plan.md §24 for the planned password UI.
                                shell_cmds = [c for c in shell_cmds if c not in sudo_cmds]
                                from backend.App.orchestration.infrastructure.manual_shell_approval import (
                                    request_manual_execution,
                                )
                                sudo_preview = ", ".join(f"`{c}`" for c in sudo_cmds[:3])
                                if len(sudo_cmds) > 3:
                                    sudo_preview += f" … (+{len(sudo_cmds) - 3})"
                                ask_manual = (
                                    f"[orchestrator] Cannot run {len(sudo_cmds)} sudo "
                                    f"command(s) — asking user to run manually: "
                                    f"{sudo_preview}\n"
                                )
                                append_task_run_log(task_dir, ask_manual.strip())
                                yield _sse_delta_line(now, request_model, ask_manual)
                                manual_done = request_manual_execution(
                                    task_id,
                                    sudo_cmds,
                                    self._task_store,
                                    reason=(
                                        "sudo is not supported by the automated "
                                        "shell (no TTY / password prompt). "
                                        "Please run these commands yourself in "
                                        "your terminal."
                                    ),
                                    cancel_event=cancel_event,
                                )
                                manual_result_line = (
                                    f"[orchestrator] user "
                                    f"{'confirmed manual execution' if manual_done else 'cancelled (command not run)'}: "
                                    f"{sudo_preview}\n"
                                )
                                append_task_run_log(task_dir, manual_result_line.strip())
                                yield _sse_delta_line(now, request_model, manual_result_line)
                                # The Done / Cancel outcome reaches the next
                                # dev iteration via the streamed transcript
                                # (history contains the "user confirmed manual
                                # execution" / "user cancelled" lines) plus the
                                # "do not emit sudo" system-prompt instruction
                                # in _dev_workspace_instructions. Nothing else
                                # needs a stash here; any per-task flags would
                                # be dead unless a downstream consumer reads
                                # them (§10.5).
                            if shell_cmds:
                                # Split commands by whether their binary is already
                                # in the env allowlist. Out-of-allowlist ones require
                                # the user to explicitly grant a per-task extension.
                                env_allow = _shell_allowlist()
                                already_allowed: list[str] = []
                                needs_allowlist: list[str] = []
                                for cmd in shell_cmds:
                                    binary = extract_command_binary(cmd)
                                    if not binary:
                                        continue
                                    if binary in env_allow:
                                        if binary not in already_allowed:
                                            already_allowed.append(binary)
                                    else:
                                        if binary not in needs_allowlist:
                                            needs_allowlist.append(binary)

                                preview = ", ".join(f"`{c}`" for c in shell_cmds[:5])
                                if len(shell_cmds) > 5:
                                    preview += f" … (+{len(shell_cmds) - 5})"
                                role_label = "devops" if agent == "devops" else "dev"
                                allowlist_suffix = ""
                                if needs_allowlist:
                                    allowlist_suffix = (
                                        " [requires allowlist extension: "
                                        + ", ".join(needs_allowlist)
                                        + "]"
                                    )
                                ask_line = (
                                    f"[orchestrator] {role_label} requests to execute "
                                    f"{len(shell_cmds)} command(s): "
                                    f"{preview}{allowlist_suffix} — awaiting approval…\n"
                                )
                                append_task_run_log(task_dir, ask_line.strip())
                                yield _sse_delta_line(now, request_model, ask_line)
                                approved = request_shell_approval(
                                    task_id,
                                    shell_cmds,
                                    self._task_store,
                                    cancel_event=cancel_event,
                                    needs_allowlist=needs_allowlist,
                                    already_allowed=already_allowed,
                                )
                                run_shell_flag = approved
                                # Apply the per-task allowlist extension BEFORE we
                                # reach ``_shell_command_allowed`` inside
                                # ``apply_workspace_pipeline`` — otherwise the new
                                # binaries would still be rejected and we'd silently
                                # drop commands the user just explicitly approved.
                                if approved and needs_allowlist:
                                    extend_runtime_shell_allowlist(needs_allowlist)
                                    logger.info(
                                        "shell approval: extended runtime allowlist "
                                        "for task=%s with %s",
                                        task_id, needs_allowlist,
                                    )
                                result_line = (
                                    f"[orchestrator] shell "
                                    f"{'approved' if approved else 'rejected'} by user"
                                    + (
                                        f" (allowlist extended: {', '.join(needs_allowlist)})"
                                        if approved and needs_allowlist
                                        else ""
                                    )
                                    + "\n"
                                )
                                append_task_run_log(task_dir, result_line.strip())
                                yield _sse_delta_line(now, request_model, result_line)
                                self._task_store.update_task(
                                    task_id,
                                    status="in_progress",
                                    agent=agent,
                                    message="continuing after shell-gate",
                                )

                        partial = apply_workspace_pipeline(
                            msg_ev,
                            workspace_path,
                            run_shell=run_shell_flag,
                        )
                        inc_list = pipeline_snapshot.setdefault(
                            "workspace_writes_incremental", []
                        )
                        inc_list.append({"step": agent, **partial})
                        log_line = (
                            f"[orchestrator] incremental workspace after {agent}: "
                            f"written={partial.get('written')!r} "
                            f"patched={partial.get('patched')!r} "
                            f"parsed={partial.get('parsed')} "
                            f"errors={partial.get('errors')!r}\n"
                        )
                        append_task_run_log(task_dir, log_line.strip())
                        yield _sse_delta_line(now, request_model, log_line)
                        for sr in partial.get("shell_runs") or []:
                            cmd = sr.get("cmd", "")
                            if sr.get("skipped"):
                                sr_line = f"[shell] skipped: {cmd} ({sr.get('reason', '')})\n"
                            elif sr.get("dry_run"):
                                sr_line = f"[shell] dry-run: {cmd}\n"
                            elif sr.get("error"):
                                sr_line = f"[shell] error: {cmd} → {sr['error']}\n"
                            else:
                                rc = sr.get("returncode", 0)
                                out_snippet = (sr.get("stdout") or "")[:200].strip()
                                sr_line = (
                                    f"[shell] {'OK' if rc == 0 else f'exit {rc}'}: {cmd}"
                                    + (f"\n  {out_snippet}" if out_snippet else "")
                                    + "\n"
                                )
                            append_task_run_log(task_dir, sr_line.strip())
                            yield _sse_delta_line(now, request_model, sr_line)
                        try:
                            prog = copy.deepcopy(pipeline_snapshot)
                            prog["workspace_writes_progress"] = partial
                            prog["note"] = "partial snapshot — stream still running"
                            (task_dir / "pipeline.json").write_text(
                                json.dumps(
                                    _pipeline_snapshot_for_disk(prog),
                                    ensure_ascii=False,
                                    indent=2,
                                ),
                                encoding="utf-8",
                            )
                        except OSError as ose:
                            logger.warning("incremental pipeline.json: %s", ose)

                # M-14 — structured audit events travel as JSON in delta.content
                # so the frontend's ``parseChatStreamEvent`` can surface them
                # as toasts. The plain text fallback (_build_agent_sse_event)
                # would produce ``[step] auto_approved: `` which the parser
                # can't distinguish from a regular log line.
                if event.get("status") == "auto_approved":
                    audit_payload = {
                        "status": "auto_approved",
                        "step": event.get("step") or event.get("agent"),
                        "rule": event.get("rule"),
                        "audit": event.get("audit"),
                        "timestamp": event.get("timestamp"),
                        "content_hash": event.get("content_hash"),
                    }
                    yield _sse_delta_line(
                        now,
                        request_model,
                        json.dumps(audit_payload, ensure_ascii=False),
                    )
                elif event.get("status") in ("automation_agent", "ring_restart"):
                    # Structured events: forward as JSON so the frontend can render
                    # automation-agent spinners and ring-restart announcements distinctly.
                    structured_payload = {
                        "status": event["status"],
                        "agent": event.get("agent", "orchestrator"),
                        "message": msg_ev,
                    }
                    structured_payload.update(
                        {k: v for k, v in event.items()
                         if k not in ("status", "agent", "message") and v is not None}
                    )
                    yield _sse_delta_line(
                        now,
                        request_model,
                        json.dumps(structured_payload, ensure_ascii=False),
                    )
                else:
                    yield _build_agent_sse_event(
                        now, request_model, event["agent"], event["status"], msg_ev
                    )

        except Exception as exc:
            err_text = str(exc)
            if isinstance(exc, HumanApprovalRequired):
                st = "awaiting_human"
                pipeline_snapshot["human_approval_step"] = exc.step
                pipeline_snapshot["partial_state"] = exc.partial_state
                pipeline_snapshot["resume_from_step"] = exc.resume_pipeline_step
            elif isinstance(exc, HumanGateTimeout):
                st = "failed"
                pipeline_snapshot["error_type"] = "human_gate_timeout"
                pipeline_snapshot["human_gate_step"] = exc.step
            elif isinstance(exc, PipelineCancelled):
                st = "cancelled"
            else:
                st = "failed"
                _ps = getattr(exc, "_partial_state", None)
                _fs = getattr(exc, "_failed_step", None)
                if isinstance(_ps, dict):
                    pipeline_snapshot["partial_state"] = _ps
                if _fs:
                    pipeline_snapshot["failed_step"] = _fs
            self._task_store.update_task(
                task_id,
                status=st,
                agent="orchestrator",
                message=err_text,
            )
            _write_agents_error_txt(task_dir, agents_dir, err_text)
            pipeline_snapshot["error"] = err_text
            _log_prefix = {
                "awaiting_human": "WAIT",
                "cancelled": "CANCELLED",
            }.get(st, "ERROR")
            append_task_run_log(task_dir, f"{_log_prefix} {st}: {err_text}")
            try:
                _ensure_task_dirs(task_dir, agents_dir)
                (task_dir / "pipeline.json").write_text(
                    json.dumps(
                        _pipeline_snapshot_for_disk(pipeline_snapshot),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except OSError as ose:
                logger.warning("Could not write pipeline.json on stream error: %s", ose)
            line = f"[orchestrator] {st}: {err_text}\n"
            yield build_error(now, request_model, line)
            yield build_done(now, request_model)
            yield "data: [DONE]\n\n"
            return

        yield from _stream_finalise(
            task_id,
            task_dir,
            pipeline_snapshot,
            workspace_path,
            workspace_apply_writes,
            cancel_event,
            now,
            request_model,
            self._task_store,
        )

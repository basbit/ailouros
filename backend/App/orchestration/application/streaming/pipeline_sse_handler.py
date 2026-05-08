from __future__ import annotations

import copy
import json
import logging
import threading
from collections.abc import Generator
from pathlib import Path
from typing import Any, Optional

from backend.App.tasks.infrastructure.task_run_log import append_task_run_log
from backend.App.orchestration.domain.exceptions import (
    HumanApprovalRequired,
    HumanGateTimeout,
    PipelineCancelled,
)
from backend.App.orchestration.application.snapshot_serializer import pipeline_snapshot_for_disk
from backend.App.shared.infrastructure.openai_sse import (
    build_agent_sse_event,
    build_done,
    build_error,
    ensure_task_dirs,
    sse_delta_line,
)
from backend.App.workspace.application.use_cases.incremental_workspace_writes import (
    apply_incremental_workspace_write,
    incremental_workspace_write_context,
    should_apply_incremental_write,
    stream_incremental_workspace_enabled,
)
from backend.App.orchestration.application.streaming.stream_finalise import (
    stream_finalise,
    write_agent_artifact,
    write_agents_error_txt,
)

logger = logging.getLogger(__name__)

_RUNTIME_STATE_KEYS_FOR_DISK: tuple[str, ...] = (
    "pipeline_metrics",
    "verification_gates",
    "verification_gate_warnings",
    "verification_contract",
    "open_defects",
    "clustered_open_defects",
    "dev_manifest",
    "dev_workspace_diff",
    "deliverable_write_mapping",
    "filesystem_truth",
    "visual_probe_manifest",
    "visual_probe_status",
    "visual_artifacts_dir",
    "must_exist_files",
    "production_paths",
    "spec_symbols",
    "placeholder_allow_list",
    "step_retries",
    "workspace_writes",
    "workspace_root",
    "task_id",
    "agent_config",
    "input",
    "user_task",
)


def _merge_runtime_state_into_snapshot(
    pipeline_snapshot: dict[str, Any],
    final_pipeline_state: dict[str, Any],
) -> None:
    if not isinstance(final_pipeline_state, dict) or not final_pipeline_state:
        return
    for state_key in _RUNTIME_STATE_KEYS_FOR_DISK:
        if state_key not in final_pipeline_state:
            continue
        runtime_value = final_pipeline_state[state_key]
        if runtime_value is None:
            continue
        if state_key in pipeline_snapshot:
            existing_value = pipeline_snapshot[state_key]
            if isinstance(existing_value, dict) and existing_value:
                continue
            if isinstance(existing_value, list) and existing_value:
                continue
            if isinstance(existing_value, str) and existing_value.strip():
                continue
        pipeline_snapshot[state_key] = runtime_value


class PipelineSSEHandler:
    def __init__(
        self,
        task_store: Any,
        artifact_writer: Any = None,
    ) -> None:
        self._task_store = task_store
        self._artifact_writer = artifact_writer or write_agent_artifact

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
        with incremental_workspace_write_context():
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
    ) -> Generator[str, None, None]:
        final_pipeline_state: dict[str, Any] = {}
        try:
            while True:
                try:
                    event = next(events_gen)
                except StopIteration as pipeline_completed:
                    if isinstance(pipeline_completed.value, dict):
                        final_pipeline_state = pipeline_completed.value
                    break

                if "agent" not in event:
                    msg_ev = str(event.get("message") or "")
                    if msg_ev:
                        meta_line = f"[orchestrator] {msg_ev}\n"
                        append_task_run_log(task_dir, meta_line.strip())
                        yield sse_delta_line(now, request_model, meta_line)
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
                        stream_incremental_workspace_enabled()
                        and should_apply_incremental_write(
                            agent, msg_ev, workspace_path, workspace_apply_writes
                        )
                    ):
                        partial: dict[str, Any] = {}
                        write_gen = apply_incremental_workspace_write(
                            agent,
                            msg_ev,
                            workspace_path,  # type: ignore[arg-type]
                            task_id,
                            self._task_store,
                            cancel_event,
                        )
                        try:
                            while True:
                                log_line = next(write_gen)
                                append_task_run_log(task_dir, log_line.strip())
                                yield sse_delta_line(now, request_model, log_line)
                        except StopIteration as stop:
                            partial = stop.value or {}

                        inc_list = pipeline_snapshot.setdefault(
                            "workspace_writes_incremental", []
                        )
                        inc_list.append({"step": agent, **partial})
                        try:
                            prog = copy.deepcopy(pipeline_snapshot)
                            prog["workspace_writes_progress"] = partial
                            prog["note"] = "partial snapshot — stream still running"
                            (task_dir / "pipeline.json").write_text(
                                json.dumps(
                                    pipeline_snapshot_for_disk(prog),
                                    ensure_ascii=False,
                                    indent=2,
                                ),
                                encoding="utf-8",
                            )
                        except OSError as io_error:
                            logger.warning("incremental pipeline.json: %s", io_error)

                if event.get("status") == "auto_approved":
                    audit_payload = {
                        "status": "auto_approved",
                        "step": event.get("step") or event.get("agent"),
                        "rule": event.get("rule"),
                        "audit": event.get("audit"),
                        "timestamp": event.get("timestamp"),
                        "content_hash": event.get("content_hash"),
                    }
                    yield sse_delta_line(
                        now,
                        request_model,
                        json.dumps(audit_payload, ensure_ascii=False),
                    )
                elif event.get("status") in ("automation_agent", "ring_restart"):
                    structured_payload = {
                        "status": event["status"],
                        "agent": event.get("agent", "orchestrator"),
                        "message": msg_ev,
                    }
                    structured_payload.update(
                        {k: v for k, v in event.items()
                         if k not in ("status", "agent", "message") and v is not None}
                    )
                    yield sse_delta_line(
                        now,
                        request_model,
                        json.dumps(structured_payload, ensure_ascii=False),
                    )
                else:
                    yield build_agent_sse_event(
                        now, request_model, event["agent"], event["status"], msg_ev
                    )

        except Exception as exc:
            err_text = str(exc)
            if isinstance(exc, HumanApprovalRequired):
                task_status = "awaiting_human"
                pipeline_snapshot["human_approval_step"] = exc.step
                pipeline_snapshot["partial_state"] = exc.partial_state
                pipeline_snapshot["resume_from_step"] = exc.resume_pipeline_step
            elif isinstance(exc, HumanGateTimeout):
                task_status = "failed"
                pipeline_snapshot["error_type"] = "human_gate_timeout"
                pipeline_snapshot["human_gate_step"] = exc.step
            elif isinstance(exc, PipelineCancelled):
                task_status = "cancelled"
            else:
                task_status = "failed"
                partial_state_attr = getattr(exc, "_partial_state", None)
                failed_step_attr = getattr(exc, "_failed_step", None)
                if isinstance(partial_state_attr, dict):
                    pipeline_snapshot["partial_state"] = partial_state_attr
                if failed_step_attr:
                    pipeline_snapshot["failed_step"] = failed_step_attr
            self._task_store.update_task(
                task_id,
                status=task_status,
                agent="orchestrator",
                message=err_text,
            )
            write_agents_error_txt(task_dir, agents_dir, err_text)
            pipeline_snapshot["error"] = err_text
            log_prefix = {
                "awaiting_human": "WAIT",
                "cancelled": "CANCELLED",
            }.get(task_status, "ERROR")
            append_task_run_log(task_dir, f"{log_prefix} {task_status}: {err_text}")
            try:
                ensure_task_dirs(task_dir, agents_dir)
                (task_dir / "pipeline.json").write_text(
                    json.dumps(
                        pipeline_snapshot_for_disk(pipeline_snapshot),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except OSError as io_error:
                logger.warning("Could not write pipeline.json on stream error: %s", io_error)
            line = f"[orchestrator] {task_status}: {err_text}\n"
            yield build_error(now, request_model, line)
            yield build_done(now, request_model)
            yield "data: [DONE]\n\n"
            return

        _merge_runtime_state_into_snapshot(pipeline_snapshot, final_pipeline_state)

        yield from stream_finalise(
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

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any, Optional, cast

from backend.App.orchestration.domain.exceptions import (
    HumanApprovalRequired,
    HumanGateTimeout,
    PipelineCancelled,
)
from backend.App.orchestration.application.routing.pipeline_graph import (
    format_human_resume_output,
    run_pipeline_stream_resume,
    final_pipeline_user_message,
    task_store_agent_label,
    validate_pipeline_steps,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.application.snapshot_serializer import pipeline_snapshot_for_disk
from backend.App.shared.infrastructure.openai_sse import (
    build_done,
    ensure_task_dirs,
    sse_delta_line,
    truncate_for_sse_delta,
)
from backend.App.tasks.infrastructure.task_run_log import append_task_run_log
from backend.App.workspace.application.use_cases.apply_pipeline_writes import (
    apply_final_workspace_writes,
    workspace_followup_lines,
)
from backend.App.orchestration.application.enforcement.ingress_security import (
    rewrite_untrusted_input,
)
from backend.App.orchestration.application.streaming.stream_finalise import write_agents_error_txt

logger = logging.getLogger(__name__)


def stream_human_resume_chunks(
    task_id: str,
    human_feedback: str,
    request_model: str,
    artifacts_root: Path,
    task_store: Any,
    cancel_event: Optional[threading.Event] = None,
    override_agent_config: Optional[dict[str, Any]] = None,
) -> Generator[str, None, None]:
    from backend.App.integrations.infrastructure.observability.logging_config import set_task_id
    set_task_id(task_id)
    now = int(time.time())
    task_dir = artifacts_root / task_id
    agents_dir = task_dir / "agents"
    ensure_task_dirs(task_dir, agents_dir)
    pipeline_path = task_dir / "pipeline.json"

    def _yield_err_line(msg: str) -> Generator[str, None, None]:
        task_store.update_task(
            task_id,
            status="failed",
            agent="orchestrator",
            message=msg,
        )
        line = f"[orchestrator] failed: {msg}\n"
        err_payload = {
            "id": f"chatcmpl-{now}",
            "object": "chat.completion.chunk",
            "created": now,
            "model": request_model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": line},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(err_payload, ensure_ascii=False)}\n\n"
        yield build_done(now, request_model)
        yield "data: [DONE]\n\n"

    if not pipeline_path.is_file():
        yield from _yield_err_line("pipeline.json not found for this task")
        return

    try:
        raw = json.loads(pipeline_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        yield from _yield_err_line(f"pipeline.json is malformed: {exc}")
        return

    partial = raw.get("partial_state")
    resume_step = raw.get("resume_from_step")
    steps = raw.get("pipeline_steps")
    if not partial or not isinstance(partial, dict):
        yield from _yield_err_line("No partial_state in pipeline.json — cannot resume this task")
        return
    if not resume_step or not isinstance(resume_step, str):
        yield from _yield_err_line("Missing resume_from_step in pipeline.json")
        return
    if not isinstance(steps, list) or not steps:
        from backend.App.orchestration.application.routing.step_registry import DEFAULT_PIPELINE_STEP_IDS
        steps = list(DEFAULT_PIPELINE_STEP_IDS)
        logger.info(
            "pipeline_steps missing in snapshot — using DEFAULT_PIPELINE_STEP_IDS (%d steps)",
            len(steps),
        )
    agent_config_for_steps: dict[str, Any] = {}
    if isinstance(override_agent_config, dict):
        agent_config_for_steps = override_agent_config
    elif isinstance(partial.get("agent_config"), dict):
        agent_config_for_steps = cast(dict[str, Any], partial["agent_config"])
    elif isinstance(raw.get("agent_config"), dict):
        agent_config_for_steps = cast(dict[str, Any], raw["agent_config"])
    try:
        validate_pipeline_steps(steps, agent_config_for_steps)
    except ValueError as exc:
        yield from _yield_err_line(str(exc))
        return

    pipeline_snapshot: dict[str, Any] = dict(raw)
    pipeline_snapshot.pop("error", None)
    rewritten_feedback = rewrite_untrusted_input(
        human_feedback,
        agent_config_for_steps,
        task_id=task_id,
        source=f"human_resume:{resume_step}",
    )
    human_line = format_human_resume_output(resume_step, rewritten_feedback.safe_text)
    pipeline_snapshot[f"{resume_step}_output"] = human_line

    workspace_root_str = str(partial.get("workspace_root") or "")
    workspace_path = (
        Path(workspace_root_str) if workspace_root_str.strip() else None
    )
    workspace_apply_writes = bool(partial.get("workspace_apply_writes"))

    task_store.update_task(
        task_id,
        status="in_progress",
        agent="orchestrator",
        message="human resume...",
    )

    append_task_run_log(task_dir, "human resume stream started")

    final_state: Optional[dict[str, Any]] = None

    try:
        gen = run_pipeline_stream_resume(
            cast(PipelineState, partial),
            steps,
            resume_step,
            rewritten_feedback.safe_text,
            cancel_event=cancel_event,
        )
        while True:
            try:
                event = next(gen)
            except StopIteration as e:
                final_state = e.value
                break
            if "agent" not in event:
                message_event = str(event.get("message") or "")
                if message_event:
                    meta_line = f"[orchestrator] {message_event}\n"
                    append_task_run_log(task_dir, meta_line.strip())
                    yield sse_delta_line(now, request_model, meta_line)
                continue
            agent = event["agent"]
            status_event = event.get("status") or ""
            message_event = str(event.get("message") or "")
            append_task_run_log(task_dir, f"{agent} {status_event}: {message_event}")
            task_store.update_task(
                task_id,
                status="in_progress",
                agent=agent,
                message=message_event,
            )

            if event.get("status") == "completed":
                out_path = agents_dir / f"{agent}.txt"
                out_path.write_text(message_event, encoding="utf-8")
                if "model" in event:
                    pipeline_snapshot[f"{agent}_model"] = event.get("model", "")
                if "provider" in event:
                    pipeline_snapshot[f"{agent}_provider"] = event.get("provider", "")
                pipeline_snapshot[f"{agent}_output"] = message_event

            stream_body = truncate_for_sse_delta(
                f"[{event['agent']}] {event['status']}: {message_event}\n"
            )
            payload = {
                "id": f"chatcmpl-{now}",
                "object": "chat.completion.chunk",
                "created": now,
                "model": request_model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "content": stream_body,
                        },
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
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
        task_store.update_task(
            task_id,
            status=task_status,
            agent="orchestrator",
            message=err_text,
        )
        write_agents_error_txt(task_dir, agents_dir, err_text)
        pipeline_snapshot["error"] = err_text
        append_task_run_log(task_dir, f"ERROR {task_status} (resume): {err_text}")
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
            logger.warning("Could not write pipeline.json on resume error: %s", io_error)
        line = f"[orchestrator] {task_status}: {err_text}\n"
        err_payload = {
            "id": f"chatcmpl-{now}",
            "object": "chat.completion.chunk",
            "created": now,
            "model": request_model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": line},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(err_payload, ensure_ascii=False)}\n\n"
        yield build_done(now, request_model)
        yield "data: [DONE]\n\n"
        return

    if final_state is None:
        yield from _yield_err_line("Pipeline returned no final state")
        return

    apply_final_workspace_writes(
        task_id,
        pipeline_snapshot,
        workspace_path,
        workspace_apply_writes,
        task_store,
        cancel_event=cancel_event,
        skip_all_shell=False,
    )

    for followup_line in workspace_followup_lines(
        workspace_path, workspace_apply_writes, pipeline_snapshot
    ):
        append_task_run_log(task_dir, followup_line.strip())
        yield sse_delta_line(now, request_model, followup_line)

    pipeline_snapshot.pop("partial_state", None)
    pipeline_snapshot.pop("resume_from_step", None)
    pipeline_snapshot.pop("human_approval_step", None)

    task_store.update_task(
        task_id,
        status="completed",
        agent=task_store_agent_label(cast(PipelineState, final_state), steps),
        message=final_pipeline_user_message(cast(PipelineState, final_state), steps),
    )
    (task_dir / "pipeline.json").write_text(
        json.dumps(
            pipeline_snapshot_for_disk(pipeline_snapshot),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    append_task_run_log(task_dir, "human resume completed, pipeline.json written")
    yield build_done(now, request_model)
    yield "data: [DONE]\n\n"


from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, cast

from backend.App.integrations.domain.ports import ObservabilityPort
from backend.App.integrations.infrastructure.cross_task_memory import persist_after_pipeline_step
from backend.App.integrations.infrastructure.observability.observability_adapter import (
    OtelObservabilityAdapter as _OtelAdapter,
)
from backend.App.orchestration.application.pipeline.ephemeral_state import (
    pop_ephemeral,
    set_ephemeral,
)
from backend.App.orchestration.application.pipeline.pipeline_hooks import run_pipeline_hooks_after, run_pipeline_hooks_before
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired

logger = logging.getLogger(__name__)

_observability: ObservabilityPort = _OtelAdapter()


def hook_wrap(
    step_id: str,
    inner: Callable[[PipelineState], dict[str, Any]],
) -> Callable[[PipelineState], dict[str, Any]]:

    def wrapped(state: PipelineState) -> dict[str, Any]:
        from backend.App.integrations.infrastructure.observability.logging_config import set_step
        from backend.App.orchestration.application.context import current_step as _cs
        set_step(step_id)
        set_ephemeral(state, "_current_step_id", step_id)
        _active_agent_config = state.get("agent_config") if isinstance(state.get("agent_config"), dict) else None
        _cs_step_token = _cs._current_step_id.set(step_id)
        _cs_cfg_token = _cs._current_agent_config.set(_active_agent_config)
        try:
            base = dict(state)
            start_time = time.perf_counter()
            try:
                from backend.App.integrations.infrastructure.llm.client import reset_thread_usage, get_and_reset_thread_usage
                reset_thread_usage()
                _has_usage_tracker = True
            except Exception:
                _has_usage_tracker = False

            with _observability.step_span_ctx(step_id, base):
                pre_hook_delta = run_pipeline_hooks_before(step_id, base)
                pre_state_delta: dict[str, Any] = dict(pre_hook_delta) if pre_hook_delta else {}
                state_with_pre_hooks: PipelineState = cast(PipelineState, {**base, **pre_state_delta})
                try:
                    step_output = inner(state_with_pre_hooks)
                except HumanApprovalRequired:
                    for k, v in state_with_pre_hooks.items():
                        if base.get(k) != v:
                            set_ephemeral(state, k, v)
                    raise
                combined_output = {**pre_state_delta, **step_output}
                after_state: PipelineState = cast(PipelineState, {**base, **combined_output})
                run_pipeline_hooks_after(step_id, after_state, step_output)
                persist_after_pipeline_step(step_id, after_state, step_output)
                try:
                    from backend.App.orchestration.application.sessions.session_transcript import (
                        append_transcript_entry,
                    )
                    _elapsed_so_far = (time.perf_counter() - start_time) * 1000.0
                    append_transcript_entry(step_id, after_state, step_output, elapsed_ms=_elapsed_so_far)
                except Exception as _transcript_exc:
                    logger.debug("session_transcript: write skipped: %s", _transcript_exc)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            try:
                _observability.record_metric(step_id, elapsed_ms)
            except Exception as record_metric_exc:
                logger.warning(
                    "step_decorator: record_metric failed for step=%s — metrics may be incomplete. error=%s",
                    step_id, record_metric_exc,
                )

            output_with_metrics = dict(step_output)
            if _has_usage_tracker:
                try:
                    token_usage = get_and_reset_thread_usage()
                    from backend.App.integrations.infrastructure.observability.step_metrics import _TOKEN_KEY_INPUT, _TOKEN_KEY_OUTPUT
                    if token_usage.get("input_tokens"):
                        output_with_metrics[_TOKEN_KEY_INPUT] = token_usage["input_tokens"]
                    if token_usage.get("output_tokens"):
                        output_with_metrics[_TOKEN_KEY_OUTPUT] = token_usage["output_tokens"]
                except Exception as usage_tracker_exc:
                    logger.warning(
                        "step_decorator: token usage extraction failed for step=%s — Tokens panel will show 0. error=%s",
                        step_id, usage_tracker_exc,
                    )
            try:
                from backend.App.integrations.infrastructure.mcp.openai_loop.loop import (
                    _last_mcp_telemetry,
                )
                from backend.App.integrations.infrastructure.observability.step_metrics import (
                    _TOKEN_KEY_FILE_READ_CACHE_HITS,
                    _TOKEN_KEY_FILE_READ_CACHE_MISSES,
                )
                _cache_hits = getattr(_last_mcp_telemetry, "file_read_cache_hits", 0) or 0
                _cache_misses = getattr(_last_mcp_telemetry, "file_read_cache_misses", 0) or 0
                if _cache_hits:
                    output_with_metrics[_TOKEN_KEY_FILE_READ_CACHE_HITS] = _cache_hits
                if _cache_misses:
                    output_with_metrics[_TOKEN_KEY_FILE_READ_CACHE_MISSES] = _cache_misses
                _last_mcp_telemetry.file_read_cache_hits = 0
                _last_mcp_telemetry.file_read_cache_misses = 0
            except Exception as mcp_telemetry_exc:
                logger.warning(
                    "step_decorator: MCP telemetry extraction failed for step=%s. error=%s",
                    step_id, mcp_telemetry_exc,
                )

            try:
                _observability.trace_step(
                    step_id,
                    {
                        "dt_ms": elapsed_ms,
                        "task_id": str(base.get("task_id") or ""),
                        "step_delta": output_with_metrics,
                    },
                )
            except Exception as trace_step_exc:
                logger.error(
                    "step_decorator: trace_step failed for step=%s task_id=%s — Tokens panel will be empty. error=%s",
                    step_id, str(base.get("task_id") or ""), trace_step_exc,
                )
            return combined_output
        finally:
            pop_ephemeral(state, "_current_step_id")
            try:
                _cs._current_agent_config.reset(_cs_cfg_token)
            except Exception as agent_config_reset_error:
                logger.debug(
                    "step_decorator: failed to reset _current_agent_config token for step=%s: %s",
                    step_id, agent_config_reset_error,
                )
            try:
                _cs._current_step_id.reset(_cs_step_token)
            except Exception as step_id_reset_error:
                logger.debug(
                    "step_decorator: failed to reset _current_step_id token for step=%s: %s",
                    step_id, step_id_reset_error,
                )

    return wrapped


class PipelineStepDecorator:

    def decorate(
        self,
        step_id: str,
        fn: Callable[[PipelineState], dict[str, Any]],
    ) -> Callable[[PipelineState], dict[str, Any]]:
        return hook_wrap(step_id, fn)

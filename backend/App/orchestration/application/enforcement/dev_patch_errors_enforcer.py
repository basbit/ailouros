from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Generator
from pathlib import Path
from string import Template
from typing import Any

from backend.App.orchestration.application.enforcement.enforcement_policy import (
    dev_patch_errors_policy,
)
from backend.App.orchestration.application.pipeline.ephemeral_state import (
    pop_ephemeral,
    set_ephemeral,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.shared.infrastructure.app_config_load import load_app_config_json

_logger = logging.getLogger(__name__)


def _prompt_section() -> dict[str, Any]:
    section = load_app_config_json("prompt_fragments.json").get("dev_patch_retry")
    if not isinstance(section, dict):
        raise RuntimeError("prompt_fragments.dev_patch_retry is not configured")
    return section


def _prompt_text(key: str) -> str:
    value = str(_prompt_section().get(key) or "")
    if not value:
        raise RuntimeError(f"prompt_fragments.dev_patch_retry.{key} is empty")
    return value


def _render_prompt_text(key: str, **values: Any) -> str:
    return Template(_prompt_text(key)).safe_substitute(**values)


def _policy_text(key: str) -> str:
    value = str(dev_patch_errors_policy().get(key) or "").strip()
    if not value:
        raise RuntimeError(f"pipeline_enforcement_policy.dev_patch_errors.{key} is empty")
    return value


def _policy_int(key: str) -> int:
    try:
        return int(dev_patch_errors_policy().get(key))
    except (TypeError, ValueError):
        return 0


def _patch_error_file_pattern() -> re.Pattern[str]:
    return re.compile(_policy_text("patch_error_file_pattern"), re.IGNORECASE)


def _environment_int(environment_key: str) -> int | None:
    environment_value = os.getenv(environment_key, "").strip()
    if environment_value.isdigit():
        return int(environment_value)
    return None


def _max_patch_retries() -> int:
    policy = dev_patch_errors_policy()
    environment_key = str(policy.get("max_retries_environment_key") or "").strip()
    if environment_key:
        environment_value = _environment_int(environment_key)
        if environment_value is not None and environment_value >= 0:
            return environment_value
    fallback_environment_key = str(policy.get("max_retries_fallback_environment_key") or "").strip()
    if fallback_environment_key:
        fallback_value = _environment_int(fallback_environment_key)
        if fallback_value is not None and fallback_value >= 0:
            return fallback_value
    return max(0, _policy_int("max_retries_default"))


def _force_swarm_file_threshold() -> int:
    environment_key = str(
        dev_patch_errors_policy().get("force_swarm_file_after_failures_environment_key") or ""
    ).strip()
    if environment_key:
        environment_value = _environment_int(environment_key)
        if environment_value is not None and environment_value >= 0:
            return environment_value
    return max(0, _policy_int("force_swarm_file_after_failures_default"))


def _file_content_max_chars() -> int:
    policy = dev_patch_errors_policy()
    environment_key = str(policy.get("file_content_max_chars_environment_key") or "").strip()
    min_chars = max(0, _policy_int("file_content_min_chars"))
    if environment_key:
        environment_value = _environment_int(environment_key)
        if environment_value is not None and environment_value >= min_chars:
            return environment_value
    return max(min_chars, _policy_int("file_content_max_chars_default"))


def _format_patch_errors_for_reprompt(patch_errors: list[Any]) -> str:
    lines: list[str] = []
    for patch_error in patch_errors:
        error_text = str(patch_error).strip()
        if error_text:
            lines.append(f"  - {error_text}")
    return "\n".join(lines)


def _extract_failed_file_paths(patch_errors: list[Any]) -> list[str]:
    extracted_paths: list[str] = []
    for patch_error in patch_errors:
        error_text = str(patch_error)
        for match in _patch_error_file_pattern().finditer(error_text):
            file_path = match.group(1).strip()
            if file_path and file_path not in extracted_paths:
                extracted_paths.append(file_path)
    return extracted_paths


def _read_current_file_content(
    workspace_root: str,
    relative_path: str,
    max_chars: int,
) -> tuple[str, bool]:
    if not workspace_root:
        return ("", False)
    try:
        full_path = (Path(workspace_root) / relative_path).resolve()
    except (OSError, ValueError):
        return ("", False)
    workspace_resolved = Path(workspace_root).resolve()
    if not str(full_path).startswith(str(workspace_resolved)):
        return ("", False)
    if not full_path.is_file():
        return ("", False)
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ("", False)
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars] + Template(
            str(dev_patch_errors_policy().get("truncated_file_suffix_template") or "")
        ).safe_substitute(max_chars=max_chars)
    return (content, True)


def _update_per_file_failure_counts(
    state_dict: dict[str, Any],
    failed_paths: list[str],
) -> dict[str, int]:
    failure_counts_raw = state_dict.get("_dev_patch_per_file_failures")
    failure_counts: dict[str, int] = {}
    if isinstance(failure_counts_raw, dict):
        for path_key, count_value in failure_counts_raw.items():
            try:
                failure_counts[str(path_key)] = int(count_value)
            except (ValueError, TypeError):
                continue
    for failed_path in failed_paths:
        failure_counts[failed_path] = failure_counts.get(failed_path, 0) + 1
    state_dict["_dev_patch_per_file_failures"] = failure_counts
    return failure_counts


def _build_file_context_blocks(
    workspace_root: str,
    failed_paths: list[str],
    failure_counts: dict[str, int],
    force_swarm_file_threshold: int,
    max_chars_per_file: int,
) -> tuple[str, list[str]]:
    if not failed_paths:
        return ("", [])
    blocks: list[str] = []
    force_swarm_file_paths: list[str] = []
    per_file_budget = max_chars_per_file
    for failed_path in failed_paths:
        failure_count = failure_counts.get(failed_path, 1)
        content, was_read = _read_current_file_content(
            workspace_root, failed_path, per_file_budget,
        )
        force_full_rewrite = (
            force_swarm_file_threshold > 0
            and failure_count >= force_swarm_file_threshold
        )
        if force_full_rewrite:
            force_swarm_file_paths.append(failed_path)
        if was_read:
            strategy_note = _render_prompt_text(
                "existing_file_force_strategy_template"
                if force_full_rewrite
                else "existing_file_patch_strategy_template",
                failure_count=failure_count,
                path=failed_path,
            )
            blocks.append(
                _render_prompt_text(
                    "existing_file_context_template",
                    path=failed_path,
                    strategy=strategy_note,
                    content=content,
                )
            )
        else:
            strategy_note = _render_prompt_text(
                "missing_file_force_strategy_template"
                if force_full_rewrite
                else "missing_file_create_strategy_template",
                failure_count=failure_count,
                path=failed_path,
            )
            blocks.append(
                _render_prompt_text(
                    "missing_file_context_template",
                    path=failed_path,
                    strategy=strategy_note,
                )
            )
    return ("\n\n".join(blocks), force_swarm_file_paths)


def _build_reprompt_text(
    *,
    errors_block: str,
    file_context_block: str,
    force_swarm_file_paths: list[str],
    force_threshold: int,
) -> str:
    sections: list[str] = [
        _prompt_text("reprompt_header"),
        _prompt_text("known_patch_error_patterns"),
        _render_prompt_text("specific_errors_template", errors_block=errors_block),
    ]
    if file_context_block:
        sections.append(
            _render_prompt_text(
                "current_file_content_template",
                file_context_block=file_context_block,
            )
        )
    if force_swarm_file_paths:
        forced_lines = "\n".join(
            _render_prompt_text("forced_file_line_template", path=forced_path)
            for forced_path in force_swarm_file_paths
        )
        sections.append(
            _render_prompt_text(
                "mandatory_full_rewrite_template",
                force_threshold=force_threshold,
                forced_file_lines=forced_lines,
            )
        )
    sections.append(_prompt_text("output_contract"))
    return "\n\n".join(sections)


def _build_progress_message(
    *,
    patch_errors: list[Any],
    failed_paths: list[str],
    retry_count: int,
    max_retries: int,
    force_swarm_file_paths: list[str],
    force_threshold: int,
) -> str:
    suffix = ""
    if force_swarm_file_paths:
        suffix = _render_prompt_text(
            "progress_suffix_template",
            file_count=len(force_swarm_file_paths),
            force_threshold=force_threshold,
        )
    return _render_prompt_text(
        "progress_message_template",
        error_count=len(patch_errors),
        file_count=len(failed_paths),
        retry_number=retry_count + 1,
        max_retries=max_retries,
        suffix=suffix,
    )


def _give_up_when_max_retries_exceeded(
    state_dict: dict[str, Any],
    patch_errors: list[Any],
    retry_count: int,
    max_retries: int,
) -> bool:
    if retry_count < max_retries:
        return False
    _logger.warning(
        "dev_patch_errors_enforcer: %d patch error(s) remain after %d retries "
        "(max=%d) — giving up on reprompt, surfacing to QA. errors=%s",
        len(patch_errors), retry_count, max_retries,
        _format_patch_errors_for_reprompt(patch_errors),
    )
    state_dict.pop("_dev_patch_errors_for_retry", None)
    return True


def enforce_dev_patch_errors(
    state: PipelineState,
    *,
    resolve_step: Callable,
    base_agent_config: dict,
    run_step_with_stream_progress: Callable,
    emit_completed: Callable,
) -> Generator[dict, None, None]:
    state_dict: dict[str, Any] = state  # type: ignore[assignment]

    patch_errors = state_dict.get("_dev_patch_errors_for_retry") or []
    if not isinstance(patch_errors, list) or not patch_errors:
        return

    retry_count = int(state_dict.get("_dev_patch_retry_count") or 0)
    max_retries = _max_patch_retries()
    if _give_up_when_max_retries_exceeded(state_dict, patch_errors, retry_count, max_retries):
        return

    workspace_root = str(state_dict.get("workspace_root") or "").strip()
    failed_paths = _extract_failed_file_paths(patch_errors)
    failure_counts = _update_per_file_failure_counts(state_dict, failed_paths)
    force_threshold = _force_swarm_file_threshold()
    file_context_block, force_swarm_file_paths = _build_file_context_blocks(
        workspace_root,
        failed_paths,
        failure_counts,
        force_threshold,
        _file_content_max_chars(),
    )
    errors_block = _format_patch_errors_for_reprompt(patch_errors)

    _logger.info(
        "dev_patch_errors_enforcer: %d patch error(s) on %d file(s) — re-prompting Dev "
        "(retry %d/%d) with file content + force_full_rewrite=%s",
        len(patch_errors), len(failed_paths), retry_count + 1, max_retries,
        force_swarm_file_paths,
    )
    yield {
        "agent": _policy_text("progress_agent_name"),
        "status": "progress",
        "message": _build_progress_message(
            patch_errors=patch_errors,
            failed_paths=failed_paths,
            retry_count=retry_count,
            max_retries=max_retries,
            force_swarm_file_paths=force_swarm_file_paths,
            force_threshold=force_threshold,
        ),
    }

    reprompt_text = _build_reprompt_text(
        errors_block=errors_block,
        file_context_block=file_context_block,
        force_swarm_file_paths=force_swarm_file_paths,
        force_threshold=force_threshold,
    )
    set_ephemeral(state, "_swarm_file_reprompt", reprompt_text)
    state_dict["_dev_patch_retry_count"] = retry_count + 1
    state_dict.pop("_dev_patch_errors_for_retry", None)
    retry_agent_name = _policy_text("retry_agent_name")

    try:
        _, dev_func = resolve_step(retry_agent_name, base_agent_config)
    except Exception as resolve_error:
        _logger.warning(
            "dev_patch_errors_enforcer: could not resolve %s step: %s",
            retry_agent_name, resolve_error,
        )
        pop_ephemeral(state, "_swarm_file_reprompt")
        return

    yield {
        "agent": retry_agent_name,
        "status": "in_progress",
        "message": _policy_text("retry_in_progress_message"),
    }
    yield from run_step_with_stream_progress(retry_agent_name, dev_func, state)
    yield emit_completed(retry_agent_name, state)
    pop_ephemeral(state, "_swarm_file_reprompt")

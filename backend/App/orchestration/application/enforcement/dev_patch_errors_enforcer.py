from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from backend.App.orchestration.application.pipeline.ephemeral_state import (
    pop_ephemeral,
    set_ephemeral,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState

_logger = logging.getLogger(__name__)

_PATCH_ERROR_FILE_RE = re.compile(r"patch\s+'([^']+)'", re.IGNORECASE)


def _max_patch_retries() -> int:
    env_value = os.getenv("SWARM_MAX_DEV_PATCH_RETRIES", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if parsed_int >= 0:
            return parsed_int
    fallback_value = os.getenv("SWARM_MAX_STEP_RETRIES", "2").strip()
    if fallback_value.isdigit():
        return int(fallback_value)
    return 2


def _force_swarm_file_threshold() -> int:
    env_value = os.getenv("SWARM_FORCE_SWARM_FILE_AFTER_N_FAILS", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if parsed_int >= 1:
            return parsed_int
    return 2


def _file_content_max_chars() -> int:
    env_value = os.getenv("SWARM_PATCH_REPROMPT_FILE_CONTENT_MAX_CHARS", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if parsed_int >= 200:
            return parsed_int
    return 6000


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
        for match in _PATCH_ERROR_FILE_RE.finditer(error_text):
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
        content = content[:max_chars] + f"\n…[file truncated at {max_chars} chars]"
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
        force_full_rewrite = failure_count >= force_swarm_file_threshold
        if force_full_rewrite:
            force_swarm_file_paths.append(failed_path)
        if was_read:
            strategy_note = (
                f"REQUIRED STRATEGY: failed {failure_count} time(s) — "
                f"emit a complete <swarm_file path='{failed_path}'>full new content</swarm_file> "
                f"replacing the entire file. Do NOT use <swarm_patch> for this file."
                if force_full_rewrite
                else (
                    f"Strategy: failed {failure_count} time(s) — use small, unique SEARCH anchors copied "
                    f"verbatim from the content below."
                )
            )
            blocks.append(
                f"### {failed_path} (current actual content from disk)\n"
                f"{strategy_note}\n"
                f"```\n{content}\n```"
            )
        else:
            if force_full_rewrite:
                blocks.append(
                    f"### {failed_path} (file does not exist on disk yet)\n"
                    f"REQUIRED STRATEGY: emit "
                    f"<swarm_file path='{failed_path}'>full content</swarm_file> "
                    f"to create the file. Failed {failure_count} time(s) with patch attempts."
                )
            else:
                blocks.append(
                    f"### {failed_path} (file does not exist on disk yet)\n"
                    f"Use <swarm_file path='{failed_path}'>full content</swarm_file> to create it."
                )
    return ("\n\n".join(blocks), force_swarm_file_paths)


_REPROMPT_HEADER_TEXT = (
    "Your previous output produced the following workspace write / patch errors. "
    "Re-emit ONLY the files that failed, correcting each error below. "
    "Do NOT repeat files that were already written successfully."
)
_KNOWN_PATCH_ERROR_PATTERNS_TEXT = (
    "Known error patterns and how to fix them:\n"
    "  - 'SEARCH must occur exactly 1 time, found 0' → the SEARCH block inside "
    "<swarm_patch> did not match the actual file bytes. Use the file content shown below "
    "as the SOURCE OF TRUTH and copy EXACT bytes (including whitespace and indentation) "
    "into SEARCH. Prefer small, unique anchors.\n"
    "  - 'no ======= separator between SEARCH and REPLACE' → your <swarm_patch> hunk is "
    "malformed. Each hunk MUST be:\n"
    "        <<<<<<< SEARCH\n"
    "        (exact existing content)\n"
    "        =======\n"
    "        (new content)\n"
    "        >>>>>>> REPLACE\n"
    "  - 'file does not exist — first SEARCH must be empty (create entire file from REPLACE)' → "
    "use <swarm_file path='…'>full content</swarm_file> for new files."
)
_REPROMPT_OUTPUT_CONTRACT_TEXT = (
    "Output contract:\n"
    "  - Wrap every file you re-emit in <swarm_file path='…'> or a correctly formed "
    "<swarm_patch path='…'> block.\n"
    "  - Do not include unchanged files.\n"
    "  - Do not add commentary between tags that could confuse the parser."
)


def _build_reprompt_text(
    *,
    errors_block: str,
    file_context_block: str,
    force_swarm_file_paths: list[str],
    force_threshold: int,
) -> str:
    sections: list[str] = [
        _REPROMPT_HEADER_TEXT,
        _KNOWN_PATCH_ERROR_PATTERNS_TEXT,
        f"Specific errors from the last attempt:\n{errors_block}",
    ]
    if file_context_block:
        sections.append(
            "## Current actual file content (read fresh from disk)\n"
            "Use this content — not the analyze_code snapshot — when constructing SEARCH "
            "blocks. The disk state may differ from earlier scans because previous patches "
            "have already modified some files.\n\n"
            + file_context_block
        )
    if force_swarm_file_paths:
        forced_lines = "\n".join(f"  - {forced_path}" for forced_path in force_swarm_file_paths)
        sections.append(
            "## MANDATORY: full file rewrite (not patch)\n"
            f"The following file(s) failed at least {force_threshold} time(s) with "
            f"<swarm_patch>. You MUST emit them as a complete "
            f"<swarm_file path='…'>full content</swarm_file> block instead. Do NOT use "
            f"<swarm_patch> for these files on this retry:\n"
            f"{forced_lines}"
        )
    sections.append(_REPROMPT_OUTPUT_CONTRACT_TEXT)
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
        suffix = (
            f"; forcing full rewrite for {len(force_swarm_file_paths)} file(s) "
            f"after {force_threshold} repeated failures"
        )
    return (
        f"Dev output produced {len(patch_errors)} patch/write error(s) on "
        f"{len(failed_paths)} file(s) (retry {retry_count + 1}/{max_retries}). "
        f"Re-prompting Dev with current file content{suffix}."
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
        "agent": "orchestrator",
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

    try:
        _, dev_func = resolve_step("dev", base_agent_config)
    except Exception as resolve_error:
        _logger.warning(
            "dev_patch_errors_enforcer: could not resolve dev step: %s", resolve_error,
        )
        pop_ephemeral(state, "_swarm_file_reprompt")
        return

    yield {"agent": "dev", "status": "in_progress", "message": "dev (patch errors retry)"}
    yield from run_step_with_stream_progress("dev", dev_func, state)
    yield emit_completed("dev", state)
    pop_ephemeral(state, "_swarm_file_reprompt")

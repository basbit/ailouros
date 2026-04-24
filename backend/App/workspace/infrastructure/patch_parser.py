from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from backend.App.shared.infrastructure.app_config_load import load_app_config_json
from backend.App.workspace.domain.asset_request import asset_requests_to_dicts, parse_asset_requests
from backend.App.shared.domain.validators import is_under
from backend.App.workspace.infrastructure.workspace_io import (
    _FileWriteAction,
    _PatchAction,
    _ShellAction,
    _UdiffAction,
    command_exec_allowed,
)

from backend.App.workspace.infrastructure.swarm_tag_parsers import (
    _PAT_FILE,
    _PAT_PATCH,
    _PAT_SHELL,
    _PAT_UDIFF,
    _PAT_BASH_FENCE,
    _apply_patch_block,
    _apply_udiff_block,
    _bash_sh_fence_body_is_only_swarm_shell,
    _collect_ordered_actions,
    _lift_swarm_shell_from_bash_sh_fences,
    _lift_swarm_shell_from_prompt_style_xml_fences,
    _markdown_fence_spans,
    _position_inside_fences,
    _run_shell_block,
    _shell_block_body_from_match,
    parse_swarm_patch_hunks,
)

__all__ = [
    "_PAT_FILE",
    "_PAT_PATCH",
    "_PAT_SHELL",
    "_PAT_UDIFF",
    "_PAT_BASH_FENCE",
    "_apply_patch_block",
    "_apply_udiff_block",
    "_bash_sh_fence_body_is_only_swarm_shell",
    "_collect_ordered_actions",
    "_lift_swarm_shell_from_bash_sh_fences",
    "_lift_swarm_shell_from_prompt_style_xml_fences",
    "_markdown_fence_spans",
    "_position_inside_fences",
    "_run_shell_block",
    "_shell_block_body_from_match",
    "parse_swarm_patch_hunks",
]

logger = logging.getLogger(__name__)

_PATCH_PARSER_CONFIG = load_app_config_json("workspace_patch_parser.json")

_PLACEHOLDER_SWARM_FILE_BODY = re.compile(
    r"^[\s.·…]{1,40}$",
    re.UNICODE,
)


def _is_placeholder_swarm_file_body(content: str) -> bool:
    stripped = content.strip()
    if not stripped:
        return True
    if _PLACEHOLDER_SWARM_FILE_BODY.fullmatch(stripped):
        return True
    if re.fullmatch(r"\.{1,3}", stripped):
        return True
    return False


def _strip_outer_markdown_fence_from_swarm_file_body(raw: str) -> str:
    body = raw.strip()
    if not body.startswith("```"):
        return raw
    newline_pos = body.find("\n")
    if newline_pos < 0:
        return raw
    rest = body[newline_pos + 1:]
    end = rest.rfind("```")
    if end < 0:
        return raw
    inner = rest[:end].strip()
    return inner if inner else raw


_PAT_SWARM_FILE_COMMENT_FENCE = re.compile(
    r"<!--\s*SWARM_FILE\s+path\s*=\s*[\"']([^\"']+)[\"']\s*-->\s*"
    r"(?:\r?\n)?"
    r"```[\w+#]*\s*\r?\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_BASE_FENCE_EXTENSIONS: str = str(_PATCH_PARSER_CONFIG["fence_base_extensions"])
_extra_fence_extensions = os.environ.get("SWARM_PATCH_PARSER_EXTRA_EXTENSIONS", "").strip()
_fence_ext_pattern = _BASE_FENCE_EXTENSIONS + (
    "|" + _extra_fence_extensions.replace(",", "|") if _extra_fence_extensions else ""
)

_PAT_FENCE_WITH_PATH_LINE = re.compile(
    r"(?m)^```(?:[\w+#]*)\s+"
    r"([a-zA-Z0-9_.][a-zA-Z0-9_./\-]*"
    rf"\.(?:{_fence_ext_pattern}))\s*"
    r"\r?\n(.*?)```",
    re.DOTALL,
)


def parse_fence_file_writes(text: str) -> list[tuple[int, str, str]]:
    if not text.strip():
        return []
    spans: list[tuple[int, int]] = []
    out: list[tuple[int, str, str]] = []
    for match in _PAT_SWARM_FILE_COMMENT_FENCE.finditer(text):
        rel_path, body = match.group(1).strip(), match.group(2).strip()
        if rel_path and body and ".." not in rel_path:
            out.append((match.start(), rel_path, body))
            spans.append((match.start(), match.end()))

    def _inside(pos: int) -> bool:
        return any(start <= pos < end for start, end in spans)

    for match in _PAT_FENCE_WITH_PATH_LINE.finditer(text):
        if _inside(match.start()):
            continue
        rel_path, body = match.group(1).strip(), match.group(2).strip()
        if rel_path and body and ".." not in rel_path:
            out.append((match.start(), rel_path, body))

    out.sort(key=lambda item: item[0])
    return out


def safe_relative_path(root: Path, rel: str) -> Path:
    if "\x00" in rel:
        raise ValueError(f"unsafe path: null byte in {rel!r}")
    rel = rel.strip().replace("\\", "/")
    if rel.startswith("/"):
        raise ValueError(f"unsafe path: absolute path not allowed {rel!r}")
    rel = rel.lstrip("/")
    if not rel or rel.startswith("..") or "/../" in rel or "/.." in rel:
        raise ValueError(f"unsafe path: {rel!r}")
    path = Path(rel)
    if ".." in path.parts:
        raise ValueError(f"unsafe path: {rel!r}")
    resolved_path = (root / rel).resolve()
    if not is_under(root.resolve(), resolved_path):
        raise ValueError(f"path escapes workspace: {rel!r}")
    return resolved_path


def parse_swarm_file_writes(text: str) -> list[tuple[str, str]]:
    return [(match.group(1).strip(), match.group(2)) for match in _PAT_FILE.finditer(text)]


_BINARY_ASSET_EXTENSIONS = frozenset(
    str(extension) for extension in _PATCH_PARSER_CONFIG["binary_asset_extensions"]
)


def _is_binary_asset_path(rel: str) -> bool:
    suffix = Path(rel).suffix.lower()
    return suffix in _BINARY_ASSET_EXTENSIONS


def _patch_body_has_search_markers(body: str) -> bool:
    return "<<<<<<< SEARCH" in body


def apply_workspace_pipeline(
    text: str,
    root: Path,
    *,
    dry_run: bool = False,
    run_shell: Optional[bool] = None,
) -> dict[str, Any]:
    root = root.resolve()
    if run_shell is None:
        run_shell = command_exec_allowed()

    text = _lift_swarm_shell_from_prompt_style_xml_fences(text)
    text = _lift_swarm_shell_from_bash_sh_fences(text)
    events = _collect_ordered_actions(text)
    written: list[str] = []
    patched: list[str] = []
    udiff_applied: list[str] = []
    write_actions: list[dict[str, str]] = []
    shell_runs: list[dict[str, Any]] = []
    errors: list[str] = []
    healed_patches: list[str] = []
    binary_assets_requested: list[str] = []
    asset_requests = asset_requests_to_dicts(parse_asset_requests(text))
    for asset_request in asset_requests:
        path = str(asset_request.get("path") or "")
        if path and path not in binary_assets_requested:
            binary_assets_requested.append(path)
    parsed = 0

    for action in events:
        if isinstance(action, _FileWriteAction):
            content = _strip_outer_markdown_fence_from_swarm_file_body(action.body)
            if _is_placeholder_swarm_file_body(content):
                errors.append(
                    f"swarm_file {action.rel!r}: skipped — empty body or placeholder "
                    f"(…); not overwriting file"
                )
                continue
            if _is_binary_asset_path(action.rel):
                binary_assets_requested.append(action.rel)
                logger.warning(
                    "swarm_file %r targets a binary asset; skipped — "
                    "request via asset pipeline (download or user upload)",
                    action.rel,
                )
                continue
            result = apply_workspace_writes(root, [(action.rel, content)], dry_run=dry_run)
            written.extend(result["written"])
            write_actions.extend(result.get("write_actions") or [])
            errors.extend(result["errors"])
            parsed += 1
        elif isinstance(action, _PatchAction):
            patch_mode = "patch_create"
            dest_exists = False
            try:
                patch_dest = safe_relative_path(root, action.rel)
                dest_exists = patch_dest.is_file()
                if dest_exists:
                    patch_mode = "patch_edit"
            except ValueError:
                patch_mode = "patch_invalid"

            if patch_mode != "patch_invalid" and _is_binary_asset_path(action.rel):
                binary_assets_requested.append(action.rel)
                logger.warning(
                    "swarm_patch %r targets a binary asset; skipped — "
                    "text patches do not apply to binary files (request via asset pipeline)",
                    action.rel,
                )
                continue

            if (
                patch_mode == "patch_create"
                and not dest_exists
                and not _patch_body_has_search_markers(action.body)
            ):
                healed_body = _strip_outer_markdown_fence_from_swarm_file_body(action.body)
                if _is_placeholder_swarm_file_body(healed_body):
                    errors.append(
                        f"swarm_patch {action.rel!r}: empty body and file does not exist"
                    )
                    continue
                result = apply_workspace_writes(
                    root, [(action.rel, healed_body)], dry_run=dry_run
                )
                written.extend(result["written"])
                write_actions.extend(result.get("write_actions") or [])
                errors.extend(result["errors"])
                healed_patches.append(action.rel)
                parsed += 1
                logger.info(
                    "swarm_patch HEALED for %r: no SEARCH/REPLACE markers + file did "
                    "not exist → promoted to swarm_file create (%d chars)",
                    action.rel, len(healed_body),
                )
                continue

            patch_ok, patch_errors = _apply_patch_block(root, action.rel, action.body, dry_run=dry_run)
            if patch_ok:
                patched.append(action.rel)
                if patch_mode != "patch_invalid":
                    write_actions.append({"path": action.rel, "mode": patch_mode})
                parsed += 1
            else:
                logger.warning(
                    "swarm_patch FAILED for %r (mode=%s): %s — "
                    "the dev agent may need to create this file with <swarm_file> first",
                    action.rel, patch_mode, patch_errors,
                )
            errors.extend(patch_errors)
        elif isinstance(action, _UdiffAction):
            udiff_mode = "udiff_create"
            try:
                udiff_dest = safe_relative_path(root, action.rel)
                if udiff_dest.is_file():
                    udiff_mode = "udiff_edit"
            except ValueError:
                udiff_mode = "udiff_invalid"
            udiff_ok, udiff_errors = _apply_udiff_block(root, action.rel, action.body, dry_run=dry_run)
            if udiff_ok:
                udiff_applied.append(action.rel)
                if udiff_mode != "udiff_invalid":
                    write_actions.append({"path": action.rel, "mode": udiff_mode})
                parsed += 1
            errors.extend(udiff_errors)
        elif isinstance(action, _ShellAction):
            shell_parsed, runs, shell_errors = _run_shell_block(
                root, action.body, dry_run=dry_run, run_shell=run_shell
            )
            parsed += shell_parsed
            shell_runs.extend(runs)
            errors.extend(shell_errors)

    if errors:
        logger.warning(
            "apply_workspace_pipeline: %d error(s) during workspace writes: %s",
            len(errors), errors,
        )
    total_changed = len(written) + len(patched) + len(udiff_applied)
    if total_changed:
        logger.info(
            "apply_workspace_pipeline: %d file(s) written, %d patched, %d udiff applied, "
            "%d error(s)",
            len(written), len(patched), len(udiff_applied), len(errors),
        )

    if healed_patches:
        logger.info(
            "apply_workspace_pipeline: healed %d malformed swarm_patch block(s) → "
            "swarm_file creates: %s",
            len(healed_patches), healed_patches,
        )
    if binary_assets_requested:
        logger.info(
            "apply_workspace_pipeline: %d binary asset(s) requested via <swarm_patch>/"
            "<swarm_file> — routed to asset pipeline (not written): %s",
            len(binary_assets_requested), binary_assets_requested,
        )

    out: dict[str, Any] = {
        "written": written,
        "patched": patched,
        "udiff_applied": udiff_applied,
        "write_actions": write_actions,
        "shell_runs": shell_runs,
        "errors": errors,
        "parsed": parsed,
        "healed_patches": healed_patches,
        "binary_assets_requested": binary_assets_requested,
        "asset_requests": asset_requests,
    }
    if not events:
        out["note"] = "no swarm_file, swarm_patch, or swarm_shell blocks"
    return out


def apply_workspace_writes(
    root: Path,
    writes: list[tuple[str, str]],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    written: list[str] = []
    write_actions: list[dict[str, str]] = []
    errors: list[str] = []

    for rel_path, content in writes:
        try:
            dest = safe_relative_path(root, rel_path)
        except ValueError as error:
            errors.append(f"{rel_path}: {error}")
            continue
        mode = "overwrite_file" if dest.exists() else "create_file"
        if dry_run:
            written.append(dest.relative_to(root).as_posix())
            write_actions.append({"path": dest.relative_to(root).as_posix(), "mode": mode})
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            if not dest.is_file():
                errors.append(f"{rel_path}: write verification failed — file does not exist after write")
                continue
            actual_size = dest.stat().st_size
            if actual_size == 0 and len(content) > 0:
                errors.append(
                    f"{rel_path}: write verification failed — file is empty "
                    f"(expected {len(content)} bytes)"
                )
                continue
            relative_posix = dest.relative_to(root).as_posix()
            written.append(relative_posix)
            write_actions.append({"path": relative_posix, "mode": mode})
        except OSError as error:
            errors.append(f"{rel_path}: {error}")

    if errors:
        logger.warning(
            "apply_workspace_writes: %d/%d files had errors: %s",
            len(errors), len(writes), errors,
        )
    return {"written": written, "write_actions": write_actions, "errors": errors}


def apply_from_agent_output(
    text: str,
    root: Path,
    *,
    dry_run: bool = False,
    run_shell: Optional[bool] = None,
) -> dict[str, Any]:
    return apply_workspace_pipeline(text, root, dry_run=dry_run, run_shell=run_shell)


WORKSPACE_SWARM_FILE_SOURCE_KEYS: tuple[str, ...] = (
    "devops_output",
    "generate_documentation_output",
    "dev_lead_output",
    "dev_output",
)

_SWARM_ACTION_MARKERS: tuple[str, ...] = (
    "<swarm_file",
    "<swarm_patch",
    "<swarm_shell",
    "<swarm-command",
    "<swarm_udiff",
)


def _extract_commands_from_bare_bash_fences(text: str) -> list[str]:
    commands: list[str] = []
    for match in _PAT_BASH_FENCE.finditer(text):
        for line in match.group(1).splitlines():
            line_text = line.strip()
            if not line_text or line_text.startswith("#") or "<swarm_" in line_text.lower():
                continue
            commands.append(line_text)
    return commands


def extract_shell_commands(text: str) -> list[str]:
    lifted = _lift_swarm_shell_from_prompt_style_xml_fences(text)
    lifted = _lift_swarm_shell_from_bash_sh_fences(lifted)
    fence_spans = _markdown_fence_spans(lifted)
    commands: list[str] = []
    for match in _PAT_SHELL.finditer(lifted):
        if _position_inside_fences(match.start(), fence_spans):
            continue
        for line in _shell_block_body_from_match(match).splitlines():
            line_text = line.strip()
            if not line_text or line_text.startswith("#"):
                continue
            commands.append(line_text)
    if not commands:
        commands = _extract_commands_from_bare_bash_fences(text)
    return commands


def text_contains_swarm_workspace_actions(text: str) -> bool:
    return any(marker in text for marker in _SWARM_ACTION_MARKERS)


def any_snapshot_output_has_swarm(state: dict[str, Any]) -> bool:
    for key, value in state.items():
        if isinstance(key, str) and key.endswith("_output") and isinstance(value, str):
            if text_contains_swarm_workspace_actions(value):
                return True
    for list_key in ("dev_task_outputs", "qa_task_outputs"):
        arr = state.get(list_key)
        if isinstance(arr, list):
            for piece in arr:
                if isinstance(piece, str) and text_contains_swarm_workspace_actions(piece):
                    return True
    return False


def collect_workspace_source_chunks(state: dict[str, Any]) -> list[str]:
    chunks: list[str] = []
    seen_keys: set[str] = set()

    steps = state.get("pipeline_steps")
    if isinstance(steps, list):
        for raw_step in steps:
            step_id = str(raw_step).strip()
            if not step_id:
                continue
            key = f"{step_id}_output"
            text = state.get(key)
            if isinstance(text, str) and text.strip():
                chunks.append(text)
                seen_keys.add(key)

    if not chunks:
        for key in WORKSPACE_SWARM_FILE_SOURCE_KEYS:
            text = state.get(key)
            if isinstance(text, str) and text.strip():
                chunks.append(text)
                seen_keys.add(key)

    if "dev_output" not in seen_keys:
        arr = state.get("dev_task_outputs")
        if isinstance(arr, list):
            for piece in arr:
                if isinstance(piece, str) and piece.strip():
                    chunks.append(piece)

    if "qa_output" not in seen_keys:
        arr = state.get("qa_task_outputs")
        if isinstance(arr, list):
            for piece in arr:
                if isinstance(piece, str) and piece.strip():
                    chunks.append(piece)

    for key in sorted(state.keys()):
        if not isinstance(key, str) or not key.endswith("_output"):
            continue
        if key in seen_keys:
            continue
        text = state.get(key)
        if not isinstance(text, str) or not text.strip():
            continue
        if not text_contains_swarm_workspace_actions(text):
            continue
        chunks.append(text)
        seen_keys.add(key)

    return chunks


def merged_workspace_source_text(state: dict[str, Any]) -> str:
    chunks = collect_workspace_source_chunks(state)
    if not chunks:
        return ""
    return "\n\n".join(chunks)


def apply_from_devops_and_dev_outputs(
    state: dict[str, Any],
    root: Path,
    *,
    dry_run: bool = False,
    run_shell: Optional[bool] = None,
) -> dict[str, Any]:
    chunks = collect_workspace_source_chunks(state)
    if not chunks:
        return {
            "written": [],
            "patched": [],
            "udiff_applied": [],
            "shell_runs": [],
            "errors": [],
            "parsed": 0,
            "note": "no pipeline step outputs and no supplemental *_output with <swarm_* tags",
        }
    merged = "\n\n".join(chunks)
    if not dry_run:
        validation_result = apply_workspace_pipeline(merged, root, dry_run=True, run_shell=False)
        validation_errors = list(validation_result.get("errors") or [])
        if validation_errors:
            error_detail = "; ".join(validation_errors[:10])
            raise RuntimeError(
                f"workspace_write_pre_validation_failed: patch validation errors detected before any files were written — "
                f"operation=apply_from_devops_and_dev_outputs "
                f"errors_count={len(validation_errors)} "
                f"errors={error_detail!r} "
                f"expected=all patches and writes are valid "
                f"actual=VALIDATION_FAILED"
            )
    return apply_workspace_pipeline(merged, root, dry_run=dry_run, run_shell=run_shell)

"""Parsers for workspace action tags: swarm_file, swarm_patch, swarm_shell, swarm_udiff.

Moved from orchestrator/workspace_io.py (Strangler Fig pattern).
Low-level tag parsers are in swarm_tag_parsers.py.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from backend.App.workspace.infrastructure.workspace_io import (
    _FileWriteAction,
    _PatchAction,
    _ShellAction,
    _UdiffAction,
    _is_under,
    _shell_command_allowed,
    command_exec_allowed,
)

# Re-export low-level parsers so existing imports keep working.
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


_PLACEHOLDER_SWARM_FILE_BODY = re.compile(
    r"^[\s.·…]{1,40}$",
    re.UNICODE,
)


def _is_placeholder_swarm_file_body(content: str) -> bool:
    """Инструкции вроде <swarm_file path=\"x\">...</swarm_file> не должны затирать реальные файлы."""
    s = content.strip()
    if not s:
        return True
    if _PLACEHOLDER_SWARM_FILE_BODY.fullmatch(s):
        return True
    if re.fullmatch(r"\.{1,3}", s):
        return True
    return False


def _strip_outer_markdown_fence_from_swarm_file_body(raw: str) -> str:
    """Модели часто кладут код внутри swarm_file в ```lang … ``` — убираем обёртку."""
    body = raw.strip()
    if not body.startswith("```"):
        return raw
    nl = body.find("\n")
    if nl < 0:
        return raw
    rest = body[nl + 1:]
    end = rest.rfind("```")
    if end < 0:
        return raw
    inner = rest[:end].strip()
    return inner if inner else raw


# Fallback, если модель не использует <swarm_file>: комментарий + fenced code или путь в строке ```
_PAT_SWARM_FILE_COMMENT_FENCE = re.compile(
    r"<!--\s*SWARM_FILE\s+path\s*=\s*[\"']([^\"']+)[\"']\s*-->\s*"
    r"(?:\r?\n)?"
    r"```[\w+#]*\s*\r?\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_BASE_FENCE_EXTENSIONS = (
    "tsx?|jsx?|vue|svelte|py|rs|go|md|jsonc?|ya?ml|yml|toml|css|scss|html|php|java|kt|swift|gradle|kts"
)
_extra_exts = os.environ.get("SWARM_PATCH_PARSER_EXTRA_EXTENSIONS", "").strip()
_fence_ext_pattern = _BASE_FENCE_EXTENSIONS + ("|" + _extra_exts.replace(",", "|") if _extra_exts else "")

_PAT_FENCE_WITH_PATH_LINE = re.compile(
    r"(?m)^```(?:[\w+#]*)\s+"
    r"([a-zA-Z0-9_.][a-zA-Z0-9_./\-]*"
    rf"\.(?:{_fence_ext_pattern}))\s*"
    r"\r?\n(.*?)```",
    re.DOTALL,
)


def parse_fence_file_writes(text: str) -> list[tuple[int, str, str]]:
    """Разбор markdown-фенсов с путём (утилита; ``apply_workspace_pipeline`` использует только ``<swarm_file>``).

    1) ``<!-- SWARM_FILE path="relative/path" -->`` сразу перед блоком ```…```
    2) Строка открытия `` ```lang path/to/file.ext`` затем код до закрывающих ```
    """
    if not text.strip():
        return []
    spans: list[tuple[int, int]] = []
    out: list[tuple[int, str, str]] = []
    for m in _PAT_SWARM_FILE_COMMENT_FENCE.finditer(text):
        rel, body = m.group(1).strip(), m.group(2).strip()
        if rel and body and ".." not in rel:
            out.append((m.start(), rel, body))
            spans.append((m.start(), m.end()))

    def _inside(pos: int) -> bool:
        return any(s <= pos < e for s, e in spans)

    for m in _PAT_FENCE_WITH_PATH_LINE.finditer(text):
        if _inside(m.start()):
            continue
        rel, body = m.group(1).strip(), m.group(2).strip()
        if rel and body and ".." not in rel:
            out.append((m.start(), rel, body))

    out.sort(key=lambda x: x[0])
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
    p = Path(rel)
    if ".." in p.parts:
        raise ValueError(f"unsafe path: {rel!r}")
    resolved_path = (root / rel).resolve()
    if not _is_under(root.resolve(), resolved_path):
        raise ValueError(f"path escapes workspace: {rel!r}")
    return resolved_path


def parse_swarm_file_writes(text: str) -> list[tuple[str, str]]:
    """Список (относительный путь, содержимое)."""
    return [(m.group(1).strip(), m.group(2)) for m in _PAT_FILE.finditer(text)]


def apply_workspace_pipeline(
    text: str,
    root: Path,
    *,
    dry_run: bool = False,
    run_shell: Optional[bool] = None,
) -> dict[str, Any]:
    """Apply workspace writes from agent output text in the order they appear.

    Processes ``<swarm_file>``, ``<swarm_patch>``, and ``<swarm_shell>`` tags
    in document order.

    Returns:
        Dict with keys ``parsed`` (int), ``written`` (list[str]),
        ``patched`` (list[str]), ``udiff_applied`` (list[str]),
        ``shell_runs`` (list[dict]), and ``errors`` (list[str]).

    Raises:
        ValueError: if ``root`` is not a valid directory (via ``validate_workspace_root``).
        OSError: if a file cannot be written (permissions, full disk, …).
    """
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
            res = apply_workspace_writes(root, [(action.rel, content)], dry_run=dry_run)
            written.extend(res["written"])
            write_actions.extend(res.get("write_actions") or [])
            errors.extend(res["errors"])
            parsed += 1
        elif isinstance(action, _PatchAction):
            patch_mode = "patch_create"
            try:
                patch_dest = safe_relative_path(root, action.rel)
                if patch_dest.is_file():
                    patch_mode = "patch_edit"
            except ValueError:
                patch_mode = "patch_invalid"
            ok, perrs = _apply_patch_block(root, action.rel, action.body, dry_run=dry_run)
            if ok:
                patched.append(action.rel)
                if patch_mode != "patch_invalid":
                    write_actions.append({"path": action.rel, "mode": patch_mode})
                parsed += 1
            errors.extend(perrs)
        elif isinstance(action, _UdiffAction):
            udiff_mode = "udiff_create"
            try:
                udiff_dest = safe_relative_path(root, action.rel)
                if udiff_dest.is_file():
                    udiff_mode = "udiff_edit"
            except ValueError:
                udiff_mode = "udiff_invalid"
            ok, uerr = _apply_udiff_block(root, action.rel, action.body, dry_run=dry_run)
            if ok:
                udiff_applied.append(action.rel)
                if udiff_mode != "udiff_invalid":
                    write_actions.append({"path": action.rel, "mode": udiff_mode})
                parsed += 1
            errors.extend(uerr)
        elif isinstance(action, _ShellAction):
            n, runs, serr = _run_shell_block(
                root, action.body, dry_run=dry_run, run_shell=run_shell
            )
            parsed += n
            shell_runs.extend(runs)
            errors.extend(serr)

    out: dict[str, Any] = {
        "written": written,
        "patched": patched,
        "udiff_applied": udiff_applied,
        "write_actions": write_actions,
        "shell_runs": shell_runs,
        "errors": errors,
        "parsed": parsed,
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
    """Записать файлы. Возвращает {written: [...], errors: [...]}."""
    root = root.resolve()
    written: list[str] = []
    write_actions: list[dict[str, str]] = []
    errors: list[str] = []

    for rel, content in writes:
        try:
            dest = safe_relative_path(root, rel)
        except ValueError as e:
            errors.append(f"{rel}: {e}")
            continue
        mode = "overwrite_file" if dest.exists() else "create_file"
        if dry_run:
            written.append(dest.relative_to(root).as_posix())
            write_actions.append({"path": dest.relative_to(root).as_posix(), "mode": mode})
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            rel_path = dest.relative_to(root).as_posix()
            written.append(rel_path)
            write_actions.append({"path": rel_path, "mode": mode})
        except OSError as e:
            errors.append(f"{rel}: {e}")

    return {"written": written, "write_actions": write_actions, "errors": errors}


def apply_from_agent_output(
    text: str,
    root: Path,
    *,
    dry_run: bool = False,
    run_shell: Optional[bool] = None,
) -> dict[str, Any]:
    return apply_workspace_pipeline(text, root, dry_run=dry_run, run_shell=run_shell)


# Если в снимке нет ``pipeline_steps`` (старые клиенты): сохраняем этот порядок слияния.
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
    """Fallback: если модель не использовала <swarm_shell>, вытащить команды из ```bash...```."""
    cmds: list[str] = []
    for m in _PAT_BASH_FENCE.finditer(text):
        for line in m.group(1).splitlines():
            line_text = line.strip()
            if not line_text or line_text.startswith("#") or "<swarm_" in line_text.lower():
                continue
            ok, _ = _shell_command_allowed(line_text)
            if ok:
                cmds.append(line_text)
    return cmds


def extract_shell_commands(text: str) -> list[str]:
    """Возвращает список команд из <swarm_shell> блоков без выполнения."""
    lifted = _lift_swarm_shell_from_prompt_style_xml_fences(text)
    lifted = _lift_swarm_shell_from_bash_sh_fences(lifted)
    fence_spans = _markdown_fence_spans(lifted)
    cmds: list[str] = []
    for m in _PAT_SHELL.finditer(lifted):
        if _position_inside_fences(m.start(), fence_spans):
            continue
        for line in _shell_block_body_from_match(m).splitlines():
            line_text = line.strip()
            if not line_text or line_text.startswith("#"):
                continue
            ok, _ = _shell_command_allowed(line_text)
            if ok:
                cmds.append(line_text)
    if not cmds:
        cmds = _extract_commands_from_bare_bash_fences(text)
    return cmds


def text_contains_swarm_workspace_actions(text: str) -> bool:
    return any(m in text for m in _SWARM_ACTION_MARKERS)


def any_snapshot_output_has_swarm(state: dict[str, Any]) -> bool:
    """Есть ли в снимке хоть один фрагмент с разметкой workspace-действий."""
    for k, v in state.items():
        if isinstance(k, str) and k.endswith("_output") and isinstance(v, str):
            if text_contains_swarm_workspace_actions(v):
                return True
    for lk in ("dev_task_outputs", "qa_task_outputs"):
        arr = state.get(lk)
        if isinstance(arr, list):
            for piece in arr:
                if isinstance(piece, str) and text_contains_swarm_workspace_actions(piece):
                    return True
    return False


def collect_workspace_source_chunks(state: dict[str, Any]) -> list[str]:
    """Тексты для ``apply_workspace_pipeline``: порядок = порядок шагов пайплайна."""
    chunks: list[str] = []
    seen_keys: set[str] = set()

    steps = state.get("pipeline_steps")
    if isinstance(steps, list):
        for raw in steps:
            sid = str(raw).strip()
            if not sid:
                continue
            key = f"{sid}_output"
            t = state.get(key)
            if isinstance(t, str) and t.strip():
                chunks.append(t)
                seen_keys.add(key)

    if not chunks:
        for key in WORKSPACE_SWARM_FILE_SOURCE_KEYS:
            t = state.get(key)
            if isinstance(t, str) and t.strip():
                chunks.append(t)
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
        t = state.get(key)
        if not isinstance(t, str) or not t.strip():
            continue
        if not text_contains_swarm_workspace_actions(t):
            continue
        chunks.append(t)
        seen_keys.add(key)

    return chunks


def merged_workspace_source_text(state: dict[str, Any]) -> str:
    """Текст как в ``apply_from_devops_and_dev_outputs`` до вызова парсера (для extract_shell_commands)."""
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
    """Разобрать все релевантные выводы шагов и применить к ``root``."""
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
    return apply_workspace_pipeline(merged, root, dry_run=dry_run, run_shell=run_shell)

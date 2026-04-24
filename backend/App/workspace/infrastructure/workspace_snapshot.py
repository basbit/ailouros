from __future__ import annotations

import logging
import os
import re as _re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_FILES = int(os.environ.get("SWARM_WORKSPACE_MAX_FILES", "200"))
_DEFAULT_MAX_TOTAL_BYTES = int(os.environ.get("SWARM_WORKSPACE_MAX_BYTES", "400000"))
_DEFAULT_MAX_FILE_BYTES = int(os.environ.get("SWARM_WORKSPACE_MAX_FILE_BYTES", "60000"))
_DEFAULT_INPUT_MAX_CHARS = int(os.environ.get("SWARM_INPUT_MAX_CHARS", "500000"))


def collect_workspace_snapshot(
    root: Path,
    *,
    max_files: Optional[int] = None,
    max_total_bytes: Optional[int] = None,
    max_file_bytes: Optional[int] = None,
) -> tuple[str, int]:
    from backend.App.workspace.infrastructure.workspace_io import _IGNORE_DIR_NAMES

    max_files_limit = max_files if max_files is not None else int(
        os.getenv("SWARM_WORKSPACE_MAX_FILES", str(_DEFAULT_MAX_FILES))
    )
    max_total_bytes_limit = max_total_bytes if max_total_bytes is not None else int(
        os.getenv("SWARM_WORKSPACE_MAX_BYTES", str(_DEFAULT_MAX_TOTAL_BYTES))
    )
    max_file_bytes_limit = max_file_bytes if max_file_bytes is not None else int(
        os.getenv("SWARM_WORKSPACE_MAX_FILE_BYTES", str(_DEFAULT_MAX_FILE_BYTES))
    )

    parts: list[str] = [f"# Workspace root: {root}\n"]
    total = 0
    count = 0
    root = root.resolve()

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in sorted(dirnames) if d not in _IGNORE_DIR_NAMES]
        for name in sorted(filenames):
            if count >= max_files_limit:
                parts.append("\n# … [snapshot truncated: max_files]\n")
                return "".join(parts), count
            file_path = Path(dirpath) / name
            relative_path = file_path.relative_to(root)
            if any(part in _IGNORE_DIR_NAMES for part in relative_path.parts):
                continue
            try:
                file_stat = file_path.stat()
            except OSError:
                continue
            if file_stat.st_size > max_file_bytes_limit:
                parts.append(
                    f"\n## {relative_path.as_posix()} (skipped, {file_stat.st_size} bytes > max)\n"
                )
                continue
            try:
                raw_bytes = file_path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw_bytes[:8000]:
                parts.append(f"\n## {relative_path.as_posix()} (skipped, binary)\n")
                continue
            try:
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                parts.append(f"\n## {relative_path.as_posix()} (skipped, non-utf8)\n")
                continue

            block = f"\n## file: {relative_path.as_posix()}\n```\n{text}\n```\n"
            if total + len(block.encode("utf-8")) > max_total_bytes_limit:
                parts.append("\n# … [snapshot truncated: max_bytes]\n")
                return "".join(parts), count

            parts.append(block)
            total += len(block.encode("utf-8"))
            count += 1

    return "".join(parts), count


async def collect_workspace_snapshot_async(
    root: Path,
    *,
    max_files: int | None = None,
    max_total_bytes: int | None = None,
    max_file_bytes: int | None = None,
) -> tuple[str, int]:
    import asyncio
    import functools
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        functools.partial(
            collect_workspace_snapshot,
            root,
            max_files=max_files,
            max_total_bytes=max_total_bytes,
            max_file_bytes=max_file_bytes,
        ),
    )


def _truncate_snapshot_to_fit(
    snapshot_section: str,
    budget_chars: int,
) -> str:
    if len(snapshot_section) <= budget_chars:
        return snapshot_section

    header_end = snapshot_section.find("\n## file:")
    if header_end == -1:
        return snapshot_section[:budget_chars]

    header = snapshot_section[: header_end]
    rest = snapshot_section[header_end:]

    blocks = _re.split(r"(?=\n## file:)", rest)

    kept: list[str] = [header]
    used = len(header)
    for block in blocks:
        if not block:
            continue
        if used + len(block) <= budget_chars:
            kept.append(block)
            used += len(block)
        else:
            kept.append("\n\n# ... [snapshot truncated by SWARM_INPUT_MAX_CHARS]\n")
            break

    return "".join(kept)


def build_input_with_workspace(
    user_task: str,
    snapshot: str,
    *,
    manifest: str = "",
    workspace_section_title: str = "Workspace snapshot",
) -> str:
    title = (workspace_section_title or "Workspace snapshot").strip() or "Workspace snapshot"
    ws_header = f"# {title}\n\n"

    parts: list[str] = []
    manifest_text = manifest.strip()
    if manifest_text:
        parts.append("# Project context (canonical)\n\n" + manifest_text)
    snapshot_text = snapshot.strip()
    if snapshot_text:
        parts.append(ws_header + snapshot_text)
    if not parts:
        return user_task

    full = (
        "\n\n---\n\n".join(parts)
        + "\n\n---\n\n# User task\n\n"
        + user_task.strip()
        + "\n"
    )

    try:
        max_chars = int(os.getenv("SWARM_INPUT_MAX_CHARS", str(_DEFAULT_INPUT_MAX_CHARS)))
    except ValueError:
        max_chars = _DEFAULT_INPUT_MAX_CHARS

    if max_chars <= 0 or len(full) <= max_chars:
        return full

    original_len = len(full)

    user_task_section = "\n\n---\n\n# User task\n\n" + user_task.strip() + "\n"
    manifest_section = ("# Project context (canonical)\n\n" + manifest_text + "\n\n---\n\n") if manifest_text else ""

    fixed_chars = len(manifest_section) + len(ws_header) + len(user_task_section)
    snapshot_budget = max_chars - fixed_chars

    if snapshot_budget <= 0:
        logger.warning(
            "Workspace snapshot removed entirely: fixed sections (%d chars) already "
            "exceed SWARM_INPUT_MAX_CHARS=%d",
            fixed_chars,
            max_chars,
        )
        if manifest_text:
            return manifest_section.rstrip("\n\n---\n\n") + user_task_section
        return user_task

    trimmed_snapshot = _truncate_snapshot_to_fit(snapshot_text, snapshot_budget)

    logger.warning(
        "Workspace section %r truncated from %d to %d chars to fit "
        "SWARM_INPUT_MAX_CHARS=%d (total input was %d chars)",
        title,
        len(snapshot_text),
        len(trimmed_snapshot),
        max_chars,
        original_len,
    )

    new_parts: list[str] = []
    if manifest_text:
        new_parts.append("# Project context (canonical)\n\n" + manifest_text)
    new_parts.append(ws_header + trimmed_snapshot)

    return (
        "\n\n---\n\n".join(new_parts)
        + "\n\n---\n\n# User task\n\n"
        + user_task.strip()
        + "\n"
    )

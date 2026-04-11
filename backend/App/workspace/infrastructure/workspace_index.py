"""Workspace file index collection helpers.

Extracted from workspace_io.py: collect_workspace_file_index and its
private helpers (_workspace_index_* functions).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _workspace_index_skip_large_bytes() -> int:
    """Файлы больше этого размера не попадают в индекс (счётчик в хвосте). 0 = не фильтровать."""
    env_value = os.getenv("SWARM_WORKSPACE_INDEX_SKIP_LARGE_BYTES", "524288").strip().lower()
    if env_value in ("", "0", "none", "off"):
        return 0
    try:
        return max(0, int(env_value))
    except ValueError:
        return 524288


def _workspace_index_max_output_chars() -> Optional[int]:
    """Верхняя граница длины текста индекса (UTF-8 символы); None = без лимита."""
    env_value = os.getenv("SWARM_WORKSPACE_INDEX_MAX_OUTPUT_CHARS", "100000").strip().lower()
    if env_value in ("", "0", "none", "off", "unlimited"):
        return None
    try:
        return max(256, int(env_value))
    except ValueError:
        return 100_000


def _workspace_index_extra_ignore_dirs() -> frozenset[str]:
    env_value = (os.getenv("SWARM_WORKSPACE_INDEX_IGNORE_DIRS") or "").strip()
    if not env_value:
        return frozenset()
    return frozenset(x.strip() for x in env_value.replace(";", ",").split(",") if x.strip())


def _append_workspace_index_omission_notes(
    lines: list[str],
    n_skip_large: int,
    n_skip_suffix: int,
    skip_large: int,
    max_out: Optional[int],
) -> None:
    total_len = sum(len(x) for x in lines)

    def _fits(extra: str) -> bool:
        return max_out is None or total_len + len(extra) <= max_out

    if n_skip_large > 0:
        note = (
            f"\n# … {n_skip_large} paths omitted (size > "
            f"SWARM_WORKSPACE_INDEX_SKIP_LARGE_BYTES={skip_large})\n"
        )
        if _fits(note):
            lines.append(note)
            total_len += len(note)
    if n_skip_suffix > 0:
        note2 = (
            f"\n# … {n_skip_suffix} paths omitted "
            f"(suffix in SWARM_WORKSPACE_INDEX_SKIP_SUFFIXES)\n"
        )
        if _fits(note2):
            lines.append(note2)


def _workspace_index_skip_suffixes() -> frozenset[str]:
    """Опционально: не включать в индекс файлы с этими суффиксами (нижний регистр, через запятую)."""
    env_value = (os.getenv("SWARM_WORKSPACE_INDEX_SKIP_SUFFIXES") or "").strip().lower()
    if not env_value:
        return frozenset()
    out: list[str] = []
    for part in env_value.replace(";", ",").split(","):
        s = part.strip().lower()
        if not s:
            continue
        if not s.startswith("."):
            s = "." + s
        out.append(s)
    return frozenset(out)


def collect_workspace_file_index(
    root: Path,
    *,
    max_paths: Optional[int] = None,
    stats_out: Optional[dict] = None,
) -> tuple[str, int]:
    """Список относительных путей и размеров без чтения содержимого файлов.

    Чтобы индекс не раздувал контекст (clarify/PM при retrieve+fallback или index_only):
    ``SWARM_WORKSPACE_INDEX_MAX_OUTPUT_CHARS`` (по умолчанию 100000),
    ``SWARM_WORKSPACE_INDEX_SKIP_LARGE_BYTES`` (по умолчанию 512KiB — путь не перечисляется),
    опционально ``SWARM_WORKSPACE_INDEX_IGNORE_DIRS``, ``SWARM_WORKSPACE_INDEX_SKIP_SUFFIXES``.

    Если передан ``stats_out`` (dict), он будет заполнен статистикой обрезания/пропуска
    для последующей отправки в SSE / логирования на уровне вызывающего кода.
    """
    from backend.App.workspace.infrastructure.workspace_io import _IGNORE_DIR_NAMES

    max_paths_limit = max_paths if max_paths is not None else int(
        os.getenv("SWARM_WORKSPACE_INDEX_MAX_PATHS", "4000")
    )
    skip_large = _workspace_index_skip_large_bytes()
    max_out = _workspace_index_max_output_chars()
    skip_suff = _workspace_index_skip_suffixes()
    blocked_dirs = _IGNORE_DIR_NAMES | _workspace_index_extra_ignore_dirs()

    root = root.resolve()
    lines: list[str] = [f"# Workspace root: {root}\n", "\n## File index\n\n"]
    total_len = sum(len(x) for x in lines)
    count = 0
    n_skip_large = 0
    n_skip_suffix = 0
    truncated_chars = False
    truncated_paths = False

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in sorted(dirnames) if d not in blocked_dirs]
        for name in sorted(filenames):
            if count >= max_paths_limit:
                lines.append(f"\n# … [index truncated: max_paths={max_paths_limit}]\n")
                _append_workspace_index_omission_notes(
                    lines, n_skip_large, n_skip_suffix, skip_large, max_out
                )
                text = "".join(lines)
                truncated_paths = True
                omitted = n_skip_large + n_skip_suffix
                logger.info(
                    "workspace_index_truncated: total_files=%d omitted_files=%d "
                    "total_chars=%d limit_paths=%d omitted_large=%d omitted_suffix=%d",
                    count,
                    omitted,
                    len(text),
                    max_paths_limit,
                    n_skip_large,
                    n_skip_suffix,
                )
                if stats_out is not None:
                    stats_out.update({
                        "total_files": count,
                        "omitted_files": omitted,
                        "total_chars": len(text),
                        "limit_paths": max_paths_limit,
                        "limit_chars": max_out,
                        "omitted_large": n_skip_large,
                        "omitted_suffix": n_skip_suffix,
                        "truncated_paths": True,
                        "truncated_chars": False,
                    })
                return text, count
            file_path = Path(dirpath) / name
            try:
                relative_path = file_path.relative_to(root)
            except ValueError:
                continue
            if any(part in blocked_dirs for part in relative_path.parts):
                continue
            file_suffix = Path(name).suffix.lower()
            if file_suffix in skip_suff:
                n_skip_suffix += 1
                continue
            try:
                file_stat = file_path.stat()
            except OSError:
                continue
            if skip_large > 0 and file_stat.st_size > skip_large:
                n_skip_large += 1
                continue

            line = f"- {relative_path.as_posix()}\t{file_stat.st_size} bytes\n"
            if max_out is not None and total_len + len(line) > max_out:
                tail = (
                    f"\n# … [index truncated: max_chars={max_out} "
                    f"(env SWARM_WORKSPACE_INDEX_MAX_OUTPUT_CHARS)]\n"
                )
                lines.append(tail)
                truncated_chars = True
                break
            lines.append(line)
            total_len += len(line)
            count += 1
        if truncated_chars:
            break

    _append_workspace_index_omission_notes(
        lines, n_skip_large, n_skip_suffix, skip_large, max_out
    )
    text = "".join(lines)
    omitted = n_skip_large + n_skip_suffix
    has_omission = omitted > 0 or truncated_chars
    if has_omission:
        logger.info(
            "workspace_index_truncated: total_files=%d omitted_files=%d "
            "total_chars=%d limit_paths=%d limit_chars=%s "
            "omitted_large=%d omitted_suffix=%d truncated_chars=%s",
            count,
            omitted,
            len(text),
            max_paths_limit,
            max_out,
            n_skip_large,
            n_skip_suffix,
            truncated_chars,
        )
    else:
        logger.info(
            "workspace file index: paths=%d chars=%d (no omissions)",
            count,
            len(text),
        )
    if stats_out is not None:
        stats_out.update({
            "total_files": count,
            "omitted_files": omitted,
            "total_chars": len(text),
            "limit_paths": max_paths_limit,
            "limit_chars": max_out,
            "omitted_large": n_skip_large,
            "omitted_suffix": n_skip_suffix,
            "truncated_paths": truncated_paths,
            "truncated_chars": truncated_chars,
        })
    return text, count

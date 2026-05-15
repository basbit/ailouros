from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RepoMapEntry:
    file_path: str
    signatures: tuple[str, ...]
    rank: float


@dataclass(frozen=True)
class RepoMap:
    entries: tuple[RepoMapEntry, ...]


_CHARS_PER_TOKEN = 4


def render_text(
    repo_map: RepoMap,
    *,
    max_tokens: int,
    token_counter: Optional[Callable[[str], int]] = None,
) -> str:
    if not repo_map.entries:
        return "# RepoMap: no source files found in workspace\n"

    def _count(text: str) -> int:
        if token_counter is not None:
            return token_counter(text)
        return max(1, len(text) // _CHARS_PER_TOKEN)

    lines: list[str] = []
    used = 0

    for entry in sorted(repo_map.entries, key=lambda e: e.rank, reverse=True):
        header = f"{entry.file_path}:\n"
        header_tokens = _count(header)
        if used + header_tokens > max_tokens:
            break
        sig_lines: list[str] = []
        sig_tokens = 0
        for sig in entry.signatures:
            line = f"  {sig}\n"
            t = _count(line)
            if used + header_tokens + sig_tokens + t > max_tokens:
                break
            sig_lines.append(line)
            sig_tokens += t
        if not sig_lines:
            continue
        lines.append(header)
        lines.extend(sig_lines)
        used += header_tokens + sig_tokens

    if not lines:
        return "# RepoMap: token budget too small to render any entry\n"

    return "".join(lines)

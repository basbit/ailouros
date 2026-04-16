"""Semantic wiki search (H-4).

Replaces the flat ~12 KB `load_wiki_context()` dump with paragraph-level
semantic retrieval. Uses the shared :mod:`embedding_service` (C-1) and
falls back to token-overlap when embeddings are unavailable, so the
caller behaviour is always defined.

Design
------

- **Granularity**: paragraph-level chunks (split on blank lines), each
  chunk keeps its source file path and the nearest preceding markdown
  header so the agent can see *where* a snippet came from.
- **Index**: in-process per ``wiki_root``. Built lazily on first
  `search()` call, invalidated when any ``*.md`` file under the root
  changes (max-mtime fingerprint).
- **Backend**: per-chunk embedding from
  :func:`get_embedding_provider`. If the provider is null or returns an
  empty vector for every chunk, we drop the matrix and serve token-overlap
  results instead — no behaviour silently disappears.
- **Output**: :func:`search_block` returns the same shape that the old
  loader produced (``"## section\\nbody\\n\\n…"``) so callers don't need to
  change their formatting.

Env vars
--------

- ``SWARM_WIKI_SEARCH_ENABLED`` — ``1``/``0`` (default ``1``). When off,
  callers should fall back to the legacy flat dump.
- ``SWARM_WIKI_SEARCH_TOPK`` — default ``8`` chunks per query.
- ``SWARM_WIKI_SEARCH_MAX_CHARS`` — total budget for the rendered block,
  default ``8000``.
- ``SWARM_WIKI_SEARCH_MIN_CHUNK_CHARS`` — chunks shorter than this are
  merged with the next paragraph (default ``80``).
- ``SWARM_WIKI_SEARCH_MAX_CHUNK_CHARS`` — chunks longer than this are
  hard-split (default ``1500``).
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from backend.App.integrations.infrastructure.embedding_service import (
    EmbeddingProvider,
    get_embedding_provider,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WikiChunk:
    """A single retrievable paragraph from the wiki."""

    rel_path: str          # e.g. "architecture/memory"
    section: str           # nearest preceding header, or "preamble"
    text: str              # the paragraph body, stripped


@dataclass(frozen=True)
class WikiHit:
    chunk: WikiChunk
    score: float


# ---------------------------------------------------------------------------
# Tunables (env-driven)
# ---------------------------------------------------------------------------


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 100_000) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def wiki_search_enabled() -> bool:
    return _env_flag("SWARM_WIKI_SEARCH_ENABLED", True)


def _topk_default() -> int:
    return _env_int("SWARM_WIKI_SEARCH_TOPK", 8, minimum=1, maximum=64)


def _max_chars_default() -> int:
    return _env_int("SWARM_WIKI_SEARCH_MAX_CHARS", 8000, minimum=512, maximum=64_000)


def _min_chunk_chars() -> int:
    return _env_int("SWARM_WIKI_SEARCH_MIN_CHUNK_CHARS", 80, minimum=10, maximum=2000)


def _max_chunk_chars() -> int:
    return _env_int("SWARM_WIKI_SEARCH_MAX_CHUNK_CHARS", 1500, minimum=200, maximum=10_000)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_HEADER_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")


def _strip_frontmatter(text: str) -> str:
    match = _FRONTMATTER_RE.match(text)
    return text[match.end():] if match else text


def _split_paragraphs(body: str) -> list[tuple[str, str]]:
    """Split a markdown body into ``(section, paragraph)`` pairs.

    The current section is the most recent ``#`` / ``##`` / … header
    above the paragraph, or ``"preamble"`` before any header is seen.
    """
    section = "preamble"
    paragraphs: list[tuple[str, str]] = []
    buffer: list[str] = []

    def _flush() -> None:
        if not buffer:
            return
        text = "\n".join(buffer).strip()
        buffer.clear()
        if text:
            paragraphs.append((section, text))

    for line in body.splitlines():
        header_match = _HEADER_RE.match(line)
        if header_match is not None:
            _flush()
            section = header_match.group(1).strip()
            continue
        if not line.strip():
            _flush()
            continue
        buffer.append(line)
    _flush()
    return paragraphs


def _coalesce_short(
    paragraphs: list[tuple[str, str]],
    *,
    min_chars: int,
    max_chars: int,
) -> list[tuple[str, str]]:
    """Merge consecutive same-section paragraphs that are below ``min_chars``.

    Keeps each chunk under ``max_chars`` — long paragraphs are kept as-is
    and split downstream (see :func:`_split_oversized`).
    """
    merged: list[tuple[str, str]] = []
    for section, text in paragraphs:
        if merged:
            prev_section, prev_text = merged[-1]
            if (
                prev_section == section
                and len(prev_text) < min_chars
                and len(prev_text) + len(text) + 2 <= max_chars
            ):
                merged[-1] = (section, f"{prev_text}\n\n{text}")
                continue
        merged.append((section, text))
    return merged


def _split_oversized(
    paragraphs: Iterable[tuple[str, str]], *, max_chars: int
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for section, text in paragraphs:
        if len(text) <= max_chars:
            out.append((section, text))
            continue
        # Hard-split on sentence boundaries when possible; otherwise on chars.
        parts = re.split(r"(?<=[.!?])\s+", text)
        chunk: list[str] = []
        chunk_len = 0
        for part in parts:
            if chunk_len + len(part) + 1 > max_chars and chunk:
                out.append((section, " ".join(chunk).strip()))
                chunk, chunk_len = [], 0
            chunk.append(part)
            chunk_len += len(part) + 1
        if chunk:
            out.append((section, " ".join(chunk).strip()))
    return out


def _chunk_file(rel_path: str, raw_text: str) -> list[WikiChunk]:
    body = _strip_frontmatter(raw_text)
    pairs = _split_paragraphs(body)
    pairs = _coalesce_short(pairs, min_chars=_min_chunk_chars(), max_chars=_max_chunk_chars())
    pairs = _split_oversized(pairs, max_chars=_max_chunk_chars())
    chunks: list[WikiChunk] = []
    for section, text in pairs:
        if not text.strip():
            continue
        chunks.append(WikiChunk(rel_path=rel_path, section=section, text=text.strip()))
    return chunks


# ---------------------------------------------------------------------------
# Token-overlap fallback (when embeddings unavailable)
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[^\w]+", flags=re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {token for token in _TOKEN_RE.split(text.lower()) if len(token) >= 3}


def _token_score(query: str, chunk: WikiChunk) -> float:
    q = _tokens(query) or _tokens(query[:200])
    if not q:
        return 0.0
    body_tokens = _tokens(chunk.text)
    section_tokens = _tokens(chunk.section)
    inter_body = len(q & body_tokens)
    inter_section = len(q & section_tokens)
    score = float(inter_body) + 0.5 * float(inter_section)
    if query.lower() and query.lower() in chunk.text.lower():
        score += 3.0
    return score


# ---------------------------------------------------------------------------
# Cosine helpers (no numpy dependency)
# ---------------------------------------------------------------------------


def _norm(vec: list[float]) -> float:
    return math.sqrt(sum(v * v for v in vec))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    norm_a = _norm(a)
    norm_b = _norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (norm_a * norm_b)


def _embed_text_for_chunk(chunk: WikiChunk) -> str:
    # Include section in the embedded text so an article on "Memory" still
    # surfaces when the query says "pattern memory" but the section header
    # carries the term.
    return f"{chunk.rel_path} :: {chunk.section}\n{chunk.text}"


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


@dataclass
class _Index:
    wiki_root: Path
    chunks: list[WikiChunk]
    vectors: Optional[list[list[float]]]   # None => embeddings unavailable
    mtime_signature: float                  # max mtime over all .md files
    provider_name: str

    def has_embeddings(self) -> bool:
        return bool(self.vectors) and any(self.vectors or [])


_index_cache: dict[str, _Index] = {}
_cache_lock = threading.Lock()


def _scan_wiki_files(wiki_root: Path) -> list[Path]:
    if not wiki_root.is_dir():
        return []
    return sorted(wiki_root.rglob("*.md"))


def _signature(files: list[Path]) -> float:
    if not files:
        return 0.0
    return max(file.stat().st_mtime for file in files)


def _build_index(wiki_root: Path, provider: EmbeddingProvider) -> _Index:
    files = _scan_wiki_files(wiki_root)
    chunks: list[WikiChunk] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("wiki_searcher: skipping %s (%s)", path, exc)
            continue
        rel = path.relative_to(wiki_root).with_suffix("").as_posix()
        chunks.extend(_chunk_file(rel, text))

    vectors: Optional[list[list[float]]] = None
    if chunks and provider.name != "null":
        try:
            raw_vectors = provider.embed([_embed_text_for_chunk(chunk) for chunk in chunks])
        except Exception as exc:
            logger.warning("wiki_searcher: embed failed (%s) — using token fallback", exc)
            raw_vectors = []
        if raw_vectors and any(vec for vec in raw_vectors):
            vectors = raw_vectors
        else:
            logger.info(
                "wiki_searcher: provider %r returned empty vectors — token fallback active",
                provider.name,
            )

    signature = _signature(files)
    logger.info(
        "wiki_searcher: indexed %s — %d chunks across %d files (provider=%s, embeddings=%s)",
        wiki_root, len(chunks), len(files), provider.name, vectors is not None,
    )
    return _Index(
        wiki_root=wiki_root,
        chunks=chunks,
        vectors=vectors,
        mtime_signature=signature,
        provider_name=provider.name,
    )


def _get_or_build_index(wiki_root: Path) -> _Index:
    key = str(wiki_root.resolve())
    provider = get_embedding_provider()
    files = _scan_wiki_files(wiki_root)
    current_sig = _signature(files)
    with _cache_lock:
        cached = _index_cache.get(key)
        if (
            cached is not None
            and cached.mtime_signature == current_sig
            and cached.provider_name == provider.name
        ):
            return cached
        index = _build_index(wiki_root, provider)
        _index_cache[key] = index
        return index


def reset_wiki_searcher_cache(wiki_root: Optional[Path] = None) -> None:
    """Drop indexed wiki(s). Used in tests and when env changes at runtime."""
    with _cache_lock:
        if wiki_root is None:
            _index_cache.clear()
            return
        _index_cache.pop(str(wiki_root.resolve()), None)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search(
    wiki_root: str | Path,
    query: str,
    *,
    k: Optional[int] = None,
) -> list[WikiHit]:
    """Return top-k :class:`WikiHit` for *query* against the wiki at *wiki_root*.

    An empty wiki, an empty query, or an unavailable index returns ``[]``.
    """
    if not query or not query.strip():
        return []
    root = Path(wiki_root)
    if not root.is_dir():
        return []
    if not wiki_search_enabled():
        return []

    index = _get_or_build_index(root)
    if not index.chunks:
        return []
    top_k = k if k is not None else _topk_default()
    top_k = max(1, min(top_k, len(index.chunks)))

    scored: list[WikiHit] = []
    if index.has_embeddings():
        provider = get_embedding_provider()
        try:
            query_vector = provider.embed([query])
        except Exception as exc:
            logger.warning("wiki_searcher: query embed failed (%s) — token fallback", exc)
            query_vector = [[]]
        query_vec = query_vector[0] if query_vector else []
        if query_vec:
            assert index.vectors is not None  # narrowed by has_embeddings
            for chunk, vector in zip(index.chunks, index.vectors):
                similarity = _cosine(query_vec, vector)
                if similarity > 0.0:
                    scored.append(WikiHit(chunk=chunk, score=similarity))

    if not scored:
        # Fallback path: token overlap. Always tried when no embedding hits.
        for chunk in index.chunks:
            score = _token_score(query, chunk)
            if score > 0.0:
                scored.append(WikiHit(chunk=chunk, score=score))

    scored.sort(key=lambda hit: hit.score, reverse=True)
    return scored[:top_k]


def search_block(
    wiki_root: str | Path,
    query: str,
    *,
    k: Optional[int] = None,
    max_chars: Optional[int] = None,
) -> str:
    """Render the search result as a markdown block for prompt injection.

    Shape mirrors the legacy ``load_wiki_context`` output (``## section …``)
    so callers can swap implementations without touching the prompt.
    Returns an empty string when there are no hits — callers must treat
    that as "no wiki memory available, do not inject a header".
    """
    hits = search(wiki_root, query, k=k)
    if not hits:
        return ""
    budget = max_chars if max_chars is not None else _max_chars_default()
    parts: list[str] = []
    total = 0
    seen_sections: set[tuple[str, str]] = set()
    for hit in hits:
        key = (hit.chunk.rel_path, hit.chunk.section)
        if key in seen_sections:
            continue  # de-dupe identical (file, section) chunks
        seen_sections.add(key)
        header = f"## {hit.chunk.rel_path} — {hit.chunk.section}"
        block = f"{header}\n{hit.chunk.text}"
        if total + len(block) + 2 > budget and parts:
            break
        parts.append(block)
        total += len(block) + 2
    if not parts:
        return ""
    logger.debug(
        "wiki_searcher: returning %d hits (%d chars) for query=%r",
        len(parts), total, query[:60],
    )
    return "\n\n".join(parts)


__all__ = [
    "WikiChunk",
    "WikiHit",
    "search",
    "search_block",
    "wiki_search_enabled",
    "reset_wiki_searcher_cache",
]

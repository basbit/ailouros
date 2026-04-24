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
from backend.App.shared.infrastructure.wiki_frontmatter import strip_frontmatter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WikiChunk:
    rel_path: str
    section: str
    text: str


@dataclass(frozen=True)
class WikiHit:
    chunk: WikiChunk
    score: float


def wiki_search_enabled() -> bool:
    raw = os.getenv("SWARM_WIKI_SEARCH_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _topk_default() -> int:
    try:
        value = int(os.getenv("SWARM_WIKI_SEARCH_TOPK", "8"))
    except (TypeError, ValueError):
        return 8
    return max(1, min(64, value))


def _max_chars_default() -> int:
    try:
        value = int(os.getenv("SWARM_WIKI_SEARCH_MAX_CHARS", "8000"))
    except (TypeError, ValueError):
        return 8000
    return max(512, min(64_000, value))


def _min_chunk_chars() -> int:
    try:
        value = int(os.getenv("SWARM_WIKI_SEARCH_MIN_CHUNK_CHARS", "80"))
    except (TypeError, ValueError):
        return 80
    return max(10, min(2000, value))


def _max_chunk_chars() -> int:
    try:
        value = int(os.getenv("SWARM_WIKI_SEARCH_MAX_CHUNK_CHARS", "1500"))
    except (TypeError, ValueError):
        return 1500
    return max(200, min(10_000, value))


_HEADER_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")


def _split_paragraphs(body: str) -> list[tuple[str, str]]:
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
    body = strip_frontmatter(raw_text)
    pairs = _split_paragraphs(body)
    pairs = _coalesce_short(pairs, min_chars=_min_chunk_chars(), max_chars=_max_chunk_chars())
    pairs = _split_oversized(pairs, max_chars=_max_chunk_chars())
    chunks: list[WikiChunk] = []
    for section, text in pairs:
        if not text.strip():
            continue
        chunks.append(WikiChunk(rel_path=rel_path, section=section, text=text.strip()))
    return chunks


_TOKEN_RE = re.compile(r"[^\w]+", flags=re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {token for token in _TOKEN_RE.split(text.lower()) if len(token) >= 3}


def _token_score(query: str, chunk: WikiChunk) -> float:
    query_tokens = _tokens(query) or _tokens(query[:200])
    if not query_tokens:
        return 0.0
    body_tokens = _tokens(chunk.text)
    section_tokens = _tokens(chunk.section)
    intersection_body = len(query_tokens & body_tokens)
    intersection_section = len(query_tokens & section_tokens)
    score = float(intersection_body) + 0.5 * float(intersection_section)
    if query.lower() and query.lower() in chunk.text.lower():
        score += 3.0
    return score


def _norm(vec: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vec))


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
    return f"{chunk.rel_path} :: {chunk.section}\n{chunk.text}"


@dataclass
class _Index:
    wiki_root: Path
    chunks: list[WikiChunk]
    vectors: Optional[list[list[float]]]
    mtime_signature: float
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
        rel_path = path.relative_to(wiki_root).with_suffix("").as_posix()
        chunks.extend(_chunk_file(rel_path, text))

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
    with _cache_lock:
        if wiki_root is None:
            _index_cache.clear()
            return
        _index_cache.pop(str(wiki_root.resolve()), None)


def search(
    wiki_root: str | Path,
    query: str,
    *,
    k: Optional[int] = None,
) -> list[WikiHit]:
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
            assert index.vectors is not None
            for chunk, vector in zip(index.chunks, index.vectors):
                similarity = _cosine(query_vec, vector)
                lex = _token_score(query, chunk)
                combined = similarity + 0.12 * min(lex, 8.0)
                if combined > 0.0:
                    scored.append(WikiHit(chunk=chunk, score=combined))

    if not scored:
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
            continue
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

"""PipelineStateSearcher — semantic search across pipeline state (§12.5).

Agents get a tool ``search_pipeline_context(query)`` that returns relevant
excerpts from any previous agent's output, without hard-coded key names.

Uses TF-IDF similarity (no external vector DB required; fast enough for
typical pipeline state sizes of ~50K chars total).

Usage::

    searcher = PipelineStateSearcher()
    searcher.index(state)
    results = searcher.search("authentication requirements", top_k=3)
    for r in results:
        print(r.key, r.score, r.excerpt[:200])
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

# State keys to index — all keys ending with _output
_OUTPUT_KEY_SUFFIX = "_output"
_MIN_CONTENT_CHARS = 50  # ignore very short values
_EXCERPT_MAX_CHARS = 500


@dataclass
class StateSearchResult:
    key: str          # state key (e.g. "ba_output")
    score: float      # TF-IDF cosine similarity [0, 1]
    excerpt: str      # representative excerpt


class PipelineStateSearcher:
    """TF-IDF based semantic search over ``*_output`` pipeline state fields.

    Re-index after each step completes (the index is cheap to build since
    pipeline state is typically < 200K chars total).
    """

    def __init__(self) -> None:
        self._docs: dict[str, str] = {}       # key → content
        self._tf_idf: dict[str, dict[str, float]] = {}  # key → term → weight
        self._idf: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index(self, state: Mapping[str, Any]) -> None:
        """Re-index all ``*_output`` fields from *state*.

        Call after each pipeline step to keep the index current.
        """
        docs: dict[str, str] = {}
        for k, v in state.items():
            if not isinstance(k, str):
                continue
            if not k.endswith(_OUTPUT_KEY_SUFFIX):
                continue
            text = str(v or "").strip()
            if len(text) >= _MIN_CONTENT_CHARS:
                docs[k] = text
        self._docs = docs
        self._build_tfidf(docs)
        logger.debug("PipelineStateSearcher: indexed %d fields", len(docs))

    def _build_tfidf(self, docs: dict[str, str]) -> None:
        n = len(docs)
        if n == 0:
            self._tf_idf = {}
            self._idf = {}
            return

        # Term frequency per document
        tf: dict[str, dict[str, int]] = {}
        df: dict[str, int] = {}
        for key, text in docs.items():
            terms = _tokenize(text)
            freq: dict[str, int] = {}
            for t in terms:
                freq[t] = freq.get(t, 0) + 1
            tf[key] = freq
            for t in set(terms):
                df[t] = df.get(t, 0) + 1

        # IDF
        idf: dict[str, float] = {
            t: math.log((1 + n) / (1 + count)) + 1.0
            for t, count in df.items()
        }

        # TF-IDF vectors
        tfidf: dict[str, dict[str, float]] = {}
        for key, freq in tf.items():
            total = sum(freq.values()) or 1
            vec: dict[str, float] = {}
            for t, count in freq.items():
                vec[t] = (count / total) * idf.get(t, 1.0)
            tfidf[key] = vec

        self._tf_idf = tfidf
        self._idf = idf

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 3) -> list[StateSearchResult]:
        """Return the top-k most relevant state fields for *query*.

        Args:
            query: Natural language query (e.g. "authentication requirements").
            top_k: Maximum number of results.

        Returns:
            List of :class:`StateSearchResult`, sorted by descending score.
        """
        if not self._tf_idf:
            return []

        q_terms = _tokenize(query)
        if not q_terms:
            return []

        # Build query vector
        q_vec: dict[str, float] = {}
        for t in q_terms:
            q_vec[t] = q_vec.get(t, 0.0) + self._idf.get(t, 1.0)

        scores: list[tuple[float, str]] = []
        for key, doc_vec in self._tf_idf.items():
            score = _cosine(q_vec, doc_vec)
            if score > 0.01:
                scores.append((score, key))

        scores.sort(reverse=True)
        results: list[StateSearchResult] = []
        for score, key in scores[:top_k]:
            text = self._docs.get(key, "")
            excerpt = _find_excerpt(text, q_terms, max_chars=_EXCERPT_MAX_CHARS)
            results.append(StateSearchResult(key=key, score=round(score, 4), excerpt=excerpt))
        return results

    def search_as_context(self, query: str, top_k: int = 3) -> str:
        """Return search results formatted as a context string for agent prompts."""
        results = self.search(query, top_k=top_k)
        if not results:
            return ""
        parts = [f"## Relevant pipeline context for: '{query}'\n"]
        for r in results:
            parts.append(f"### {r.key} (relevance: {r.score:.2f})\n{r.excerpt}\n")
        return "\n".join(parts)


# ------------------------------------------------------------------
# Module-level instance stored in pipeline state
# ------------------------------------------------------------------

_STATE_SEARCHER_KEY = "_state_searcher"


def get_state_searcher(state: Any) -> PipelineStateSearcher:
    """Get or create the :class:`PipelineStateSearcher` attached to *state*."""
    searcher = state.get(_STATE_SEARCHER_KEY)
    if not isinstance(searcher, PipelineStateSearcher):
        searcher = PipelineStateSearcher()
        state[_STATE_SEARCHER_KEY] = searcher
    return searcher


def index_state(state: Any) -> None:
    """Re-index all output fields in *state*.  Call after each step completes."""
    get_state_searcher(state).index(state)


def search_context(state: Any, query: str, top_k: int = 3) -> str:
    """Convenience: search *state* and return formatted context string."""
    return get_state_searcher(state).search_as_context(query, top_k=top_k)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]{2,}")


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    dot = sum(a.get(t, 0.0) * w for t, w in b.items())
    mag_a = math.sqrt(sum(v * v for v in a.values())) or 1e-10
    mag_b = math.sqrt(sum(v * v for v in b.values())) or 1e-10
    return dot / (mag_a * mag_b)


def _find_excerpt(text: str, query_terms: list[str], max_chars: int) -> str:
    """Find the best excerpt from *text* containing as many *query_terms* as possible."""
    if len(text) <= max_chars:
        return text
    # Find position of first query term hit
    lower = text.lower()
    best_pos = 0
    for t in query_terms:
        pos = lower.find(t)
        if pos >= 0:
            best_pos = max(0, pos - 100)
            break
    return text[best_pos: best_pos + max_chars] + "…"

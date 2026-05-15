from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

_OUTPUT_KEY_SUFFIX = "_output"
_MIN_CONTENT_CHARS = 50
_EXCERPT_MAX_CHARS = 500


@dataclass
class StateSearchResult:
    key: str
    score: float
    excerpt: str


class PipelineStateSearcher:

    def __init__(self) -> None:
        self._docs: dict[str, str] = {}
        self._tf_idf: dict[str, dict[str, float]] = {}
        self._idf: dict[str, float] = {}

    def index(self, state: Mapping[str, Any]) -> None:
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

        idf: dict[str, float] = {
            t: math.log((1 + n) / (1 + count)) + 1.0
            for t, count in df.items()
        }

        tfidf: dict[str, dict[str, float]] = {}
        for key, freq in tf.items():
            total = sum(freq.values()) or 1
            vec: dict[str, float] = {}
            for t, count in freq.items():
                vec[t] = (count / total) * idf.get(t, 1.0)
            tfidf[key] = vec

        self._tf_idf = tfidf
        self._idf = idf

    def search(self, query: str, top_k: int = 3) -> list[StateSearchResult]:
        if not self._tf_idf:
            return []

        q_terms = _tokenize(query)
        if not q_terms:
            return []

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
        results = self.search(query, top_k=top_k)
        if not results:
            return ""
        parts = [f"## Relevant pipeline context for: '{query}'\n"]
        for r in results:
            parts.append(f"### {r.key} (relevance: {r.score:.2f})\n{r.excerpt}\n")
        return "\n".join(parts)


_STATE_SEARCHER_KEY = "_state_searcher"


def get_state_searcher(state: Any) -> PipelineStateSearcher:
    searcher = state.get(_STATE_SEARCHER_KEY)
    if not isinstance(searcher, PipelineStateSearcher):
        searcher = PipelineStateSearcher()
        state[_STATE_SEARCHER_KEY] = searcher
    return searcher


def index_state(state: Any) -> None:
    get_state_searcher(state).index(state)


def search_context(state: Any, query: str, top_k: int = 3) -> str:
    return get_state_searcher(state).search_as_context(query, top_k=top_k)


_WORD_RE = re.compile(r"[a-z0-9]{2,}")


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    dot = sum(a.get(t, 0.0) * w for t, w in b.items())
    mag_a = math.sqrt(sum(v * v for v in a.values())) or 1e-10
    mag_b = math.sqrt(sum(v * v for v in b.values())) or 1e-10
    return dot / (mag_a * mag_b)


def _find_excerpt(text: str, query_terms: list[str], max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    lower = text.lower()
    best_pos = 0
    for t in query_terms:
        pos = lower.find(t)
        if pos >= 0:
            best_pos = max(0, pos - 100)
            break
    return text[best_pos: best_pos + max_chars] + "…"

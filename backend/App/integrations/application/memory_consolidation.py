"""Semantic memory consolidation — "Dream" pass (K-7).

Clusters recent cross-task episodes by textual similarity, extracts reusable
patterns via LLM, and stores them into pattern_memory with provenance.

Configuration (env):
  SWARM_DREAM_MIN_CLUSTER_SIZE  — minimum episodes per cluster (default: 3)
  SWARM_DREAM_SIMILARITY_THRESH — cosine similarity threshold 0..1 (default: 0.25)
  SWARM_DREAM_NAMESPACE         — pattern_memory namespace (default: "consolidated")
  SWARM_DREAM_EPISODE_LIMIT     — max episodes to load per run (default: 200)

Rules (INV-1): every consolidation run and each stored pattern is logged.
"""
from __future__ import annotations

import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MIN_CLUSTER_SIZE = int(os.getenv("SWARM_DREAM_MIN_CLUSTER_SIZE", "3"))
_SIMILARITY_THRESH = float(os.getenv("SWARM_DREAM_SIMILARITY_THRESH", "0.25"))
_NAMESPACE = os.getenv("SWARM_DREAM_NAMESPACE", "consolidated")
_EPISODE_LIMIT = int(os.getenv("SWARM_DREAM_EPISODE_LIMIT", "200"))


# ---------------------------------------------------------------------------
# TF-IDF helpers (no sklearn dependency — stdlib only)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """Lowercase word tokenizer, min length 3."""
    return [t for t in re.split(r"[^\w]+", text.lower()) if len(t) >= 3]


def _tf(tokens: list[str]) -> dict[str, float]:
    """Term frequency: raw count normalised by document length."""
    if not tokens:
        return {}
    freq: dict[str, int] = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    n = len(tokens)
    return {t: c / n for t, c in freq.items()}


def _build_tfidf_vectors(
    docs: list[list[str]],
) -> list[dict[str, float]]:
    """Build TF-IDF vectors for a list of tokenised documents."""
    n_docs = len(docs)
    if n_docs == 0:
        return []

    # document frequency
    df: dict[str, int] = {}
    for tokens in docs:
        for t in set(tokens):
            df[t] = df.get(t, 0) + 1

    vectors: list[dict[str, float]] = []
    for tokens in docs:
        tf = _tf(tokens)
        vec: dict[str, float] = {}
        for term, tf_val in tf.items():
            idf = math.log((1 + n_docs) / (1 + df.get(term, 0))) + 1.0
            vec[term] = tf_val * idf
        vectors.append(vec)
    return vectors


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two sparse TF-IDF vectors."""
    if not a or not b:
        return 0.0
    dot = sum(a.get(t, 0.0) * v for t, v in b.items())
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# MemoryConsolidator
# ---------------------------------------------------------------------------

class MemoryConsolidator:
    """Clusters episodes and distils them into pattern_memory.

    Usage::

        from backend.App.integrations.infrastructure.cross_task_memory import _LOCAL_EPISODES
        from backend.App.integrations.infrastructure.pattern_memory import pattern_memory_path_for_state

        consolidator = MemoryConsolidator(llm_backend=my_llm)
        stats = consolidator.run_consolidation(namespace="default", pattern_path=path)
    """

    def __init__(self, llm_backend: Any | None = None) -> None:
        """
        Args:
            llm_backend: Object with a ``chat(messages, model, **kw) -> (str, dict)``
                         method (LLMBackend protocol). If None, LLM extraction is
                         skipped and only token-overlap summaries are stored.
        """
        self._llm = llm_backend

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_consolidation(
        self,
        namespace: str = "default",
        *,
        pattern_path: Path | None = None,
        episode_limit: int = _EPISODE_LIMIT,
    ) -> dict[str, int]:
        """Run a full consolidation pass.

        Args:
            namespace: cross_task_memory namespace to read episodes from.
            pattern_path: destination pattern_memory JSON file. Defaults to
                          ``<cwd>/.swarm/pattern_memory.json``.
            episode_limit: maximum number of recent episodes to process.

        Returns:
            Stats dict with keys ``episodes_loaded``, ``clusters_formed``,
            ``patterns_stored``, ``episodes_marked``.
        """
        from backend.App.integrations.infrastructure.cross_task_memory import _load_episodes

        if pattern_path is None:
            pattern_path = (Path.cwd() / ".swarm" / "pattern_memory.json").resolve()

        t_start = time.monotonic()
        episodes: list[dict[str, Any]] = _load_episodes(namespace, limit=episode_limit)
        # Filter already-consolidated episodes
        pending = [ep for ep in episodes if not ep.get("consolidated")]

        logger.info(
            "MemoryConsolidator: namespace=%s episodes_loaded=%d pending=%d",
            namespace,
            len(episodes),
            len(pending),
        )

        if len(pending) < _MIN_CLUSTER_SIZE:
            logger.info(
                "MemoryConsolidator: not enough pending episodes (%d < %d), skipping",
                len(pending),
                _MIN_CLUSTER_SIZE,
            )
            return {
                "episodes_loaded": len(episodes),
                "clusters_formed": 0,
                "patterns_stored": 0,
                "episodes_marked": 0,
            }

        clusters = self._cluster_episodes(pending)
        patterns_stored = 0
        episodes_marked = 0

        for cluster_idx, cluster in enumerate(clusters):
            if len(cluster) < _MIN_CLUSTER_SIZE:
                continue

            pattern_text, pattern_key = self._extract_patterns(cluster)
            if not pattern_text.strip():
                continue

            provenance_ids = [
                ep.get("task_id", "") or ep.get("step", f"ep_{i}")
                for i, ep in enumerate(cluster)
            ]

            from backend.App.integrations.infrastructure.pattern_memory import (
                store_consolidated_pattern,
            )
            store_consolidated_pattern(
                pattern_key=pattern_key,
                value=pattern_text,
                provenance_ids=provenance_ids,
                path=pattern_path,
            )
            patterns_stored += 1

            # Mark source episodes as consolidated
            self._mark_episodes_consolidated(namespace, cluster)
            episodes_marked += len(cluster)

            logger.info(
                "MemoryConsolidator: cluster=%d size=%d pattern_key=%s provenance=%s",
                cluster_idx,
                len(cluster),
                pattern_key,
                provenance_ids[:3],
            )

        elapsed = time.monotonic() - t_start
        stats = {
            "episodes_loaded": len(episodes),
            "clusters_formed": len(clusters),
            "patterns_stored": patterns_stored,
            "episodes_marked": episodes_marked,
        }
        logger.info(
            "MemoryConsolidator: done elapsed=%.2fs stats=%s",
            elapsed,
            stats,
        )
        return stats

    # ------------------------------------------------------------------
    # Clustering
    # ------------------------------------------------------------------

    def _cluster_episodes(
        self, episodes: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        """Greedy single-linkage clustering by TF-IDF cosine similarity.

        Episodes whose pairwise cosine similarity exceeds _SIMILARITY_THRESH
        are grouped together. Returns a list of clusters (each a list of
        episode dicts). Unclustered singletons are included as size-1 lists.
        """
        if not episodes:
            return []

        bodies = [str(ep.get("body") or "") for ep in episodes]
        token_lists = [_tokenize(b) for b in bodies]
        vectors = _build_tfidf_vectors(token_lists)

        n = len(episodes)
        cluster_id = list(range(n))  # union-find via direct labels

        for i in range(n):
            for j in range(i + 1, n):
                sim = _cosine(vectors[i], vectors[j])
                if sim >= _SIMILARITY_THRESH:
                    # merge j's cluster into i's
                    old_id = cluster_id[j]
                    new_id = cluster_id[i]
                    if old_id != new_id:
                        for k in range(n):
                            if cluster_id[k] == old_id:
                                cluster_id[k] = new_id

        # Group episodes by cluster label
        groups: dict[int, list[dict[str, Any]]] = {}
        for idx, label in enumerate(cluster_id):
            groups.setdefault(label, []).append(episodes[idx])

        return list(groups.values())

    # ------------------------------------------------------------------
    # Pattern extraction
    # ------------------------------------------------------------------

    def _extract_patterns(
        self, cluster: list[dict[str, Any]]
    ) -> tuple[str, str]:
        """Extract a reusable pattern text and a key from a cluster.

        Returns:
            (pattern_text, pattern_key)
        """
        combined_body = "\n\n---\n\n".join(
            str(ep.get("body") or "").strip() for ep in cluster
        )
        step_labels = sorted({str(ep.get("step") or "unknown") for ep in cluster})
        pattern_key = "dream:" + ":".join(step_labels[:4])

        if self._llm is not None:
            pattern_text = self._llm_extract(combined_body, step_labels)
        else:
            # Fallback: token-overlap summary — top frequent tokens as keywords
            tokens = _tokenize(combined_body)
            freq: dict[str, int] = {}
            for t in tokens:
                freq[t] = freq.get(t, 0) + 1
            top_tokens = sorted(freq, key=lambda t: -freq[t])[:20]
            pattern_text = (
                f"Consolidated pattern from {len(cluster)} episodes "
                f"(steps: {', '.join(step_labels)}).\n"
                f"Key terms: {', '.join(top_tokens)}.\n\n"
                f"Sample episode body:\n{combined_body[:1200]}"
            )

        return pattern_text, pattern_key

    def _llm_extract(self, combined_body: str, step_labels: list[str]) -> str:
        """Call LLM to extract a concise reusable pattern."""
        prompt = (
            f"You are a memory consolidation agent. The following are {len(step_labels)} "
            f"related pipeline episode outputs from steps: {', '.join(step_labels)}.\n\n"
            "Extract a concise, reusable pattern or lesson that can guide future runs. "
            "Be specific and actionable. Max 400 words.\n\n"
            f"---\n{combined_body[:6000]}\n---"
        )
        try:
            text, _ = self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                model=os.getenv("SWARM_MEMORY_CONSOLIDATION_MODEL", "claude-haiku-4-5"),
                temperature=0.2,
            )
            return str(text).strip()
        except Exception as exc:  # pragma: no cover
            logger.warning("MemoryConsolidator: LLM extraction failed (%s), using fallback", exc)
            tokens = _tokenize(combined_body)
            freq: dict[str, int] = {}
            for t in tokens:
                freq[t] = freq.get(t, 0) + 1
            top_tokens = sorted(freq, key=lambda t: -freq[t])[:15]
            return (
                f"[LLM unavailable] Pattern from steps {', '.join(step_labels)}. "
                f"Key terms: {', '.join(top_tokens)}."
            )

    # ------------------------------------------------------------------
    # Episode marking
    # ------------------------------------------------------------------

    def _mark_episodes_consolidated(
        self,
        namespace: str,
        cluster: list[dict[str, Any]],
    ) -> None:
        """Mark episodes in-place as consolidated=True.

        Works for both local (_LOCAL_EPISODES) and Redis storage.
        For Redis, patches the serialised JSON and re-stores the item.
        """
        from backend.App.integrations.infrastructure.cross_task_memory import (
            _LOCAL_EPISODES,
            _list_key,
            _redis,
        )

        # Mark local dict entries (in-process memory)
        local_bucket = _LOCAL_EPISODES.get(namespace, [])
        cluster_bodies = {str(ep.get("body") or "") for ep in cluster}
        for ep in local_bucket:
            if str(ep.get("body") or "") in cluster_bodies:
                ep["consolidated"] = True

        # Best-effort Redis update: iterate list, patch matching entries
        r = _redis()
        if not r:
            return
        key = _list_key(namespace)
        try:
            import json
            raw_items = r.lrange(key, 0, -1)
            for idx, raw in enumerate(raw_items):
                try:
                    ep = json.loads(raw)
                except Exception:
                    continue
                if str(ep.get("body") or "") in cluster_bodies and not ep.get("consolidated"):
                    ep["consolidated"] = True
                    r.lset(key, idx, json.dumps(ep, ensure_ascii=False))
        except Exception as exc:  # pragma: no cover
            logger.debug("MemoryConsolidator: Redis mark failed (%s)", exc)

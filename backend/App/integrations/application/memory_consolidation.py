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


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", text.lower()) if len(t) >= 3]


def _tf(tokens: list[str]) -> dict[str, float]:
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
    n_docs = len(docs)
    if n_docs == 0:
        return []

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
    if not a or not b:
        return 0.0
    dot = sum(a.get(t, 0.0) * v for t, v in b.items())
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class MemoryConsolidator:
    def __init__(self, llm_backend: Any | None = None) -> None:
        self._llm = llm_backend

    def run_consolidation(
        self,
        namespace: str = "default",
        *,
        pattern_path: Path | None = None,
        episode_limit: int = _EPISODE_LIMIT,
    ) -> dict[str, int]:
        from backend.App.integrations.infrastructure.cross_task_memory import _load_episodes

        if pattern_path is None:
            pattern_path = (Path.cwd() / ".swarm" / "pattern_memory.json").resolve()

        t_start = time.monotonic()
        episodes: list[dict[str, Any]] = _load_episodes(namespace, limit=episode_limit)
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

            self._mark_episodes_consolidated(namespace, cluster)
            episodes_marked += len(cluster)

            logger.info(
                "MemoryConsolidator: cluster=%d size=%d pattern_key=%s provenance=%s",
                cluster_idx,
                len(cluster),
                pattern_key,
                provenance_ids[:3],
            )

        dream_patterns = 0
        try:
            from backend.App.integrations.infrastructure.memory_consolidation import (
                dream_pass,
            )
            dream_patterns = dream_pass(namespace)
        except Exception as exc:
            logger.warning("MemoryConsolidator: dream_pass failed: %s", exc)

        elapsed = time.monotonic() - t_start
        stats = {
            "episodes_loaded": len(episodes),
            "clusters_formed": len(clusters),
            "patterns_stored": patterns_stored,
            "episodes_marked": episodes_marked,
            "dream_patterns": dream_patterns,
        }
        logger.info(
            "MemoryConsolidator: done elapsed=%.2fs stats=%s",
            elapsed,
            stats,
        )
        return stats

    def _cluster_episodes(
        self, episodes: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        if not episodes:
            return []

        bodies = [str(ep.get("body") or "") for ep in episodes]
        token_lists = [_tokenize(b) for b in bodies]
        vectors = _build_tfidf_vectors(token_lists)

        n = len(episodes)
        cluster_id = list(range(n))

        for i in range(n):
            for j in range(i + 1, n):
                sim = _cosine(vectors[i], vectors[j])
                if sim >= _SIMILARITY_THRESH:
                    old_id = cluster_id[j]
                    new_id = cluster_id[i]
                    if old_id != new_id:
                        for k in range(n):
                            if cluster_id[k] == old_id:
                                cluster_id[k] = new_id

        groups: dict[int, list[dict[str, Any]]] = {}
        for idx, label in enumerate(cluster_id):
            groups.setdefault(label, []).append(episodes[idx])

        return list(groups.values())

    def _extract_patterns(
        self, cluster: list[dict[str, Any]]
    ) -> tuple[str, str]:
        combined_body = "\n\n---\n\n".join(
            str(ep.get("body") or "").strip() for ep in cluster
        )
        step_labels = sorted({str(ep.get("step") or "unknown") for ep in cluster})
        pattern_key = "dream:" + ":".join(step_labels[:4])

        if self._llm is not None:
            pattern_text = self._llm_extract(combined_body, step_labels)
        else:
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
        prompt = (
            f"You are a memory consolidation agent. The following are {len(step_labels)} "
            f"related pipeline episode outputs from steps: {', '.join(step_labels)}.\n\n"
            "Extract a concise, reusable pattern or lesson that can guide future runs. "
            "Be specific and actionable. Max 400 words.\n\n"
            f"---\n{combined_body[:6000]}\n---"
        )
        if self._llm is None:
            raise RuntimeError(
                "_llm_extract called without an LLM backend; caller must check self._llm is not None"
            )
        llm = self._llm
        try:
            text, _ = llm.chat(
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

    def _mark_episodes_consolidated(
        self,
        namespace: str,
        cluster: list[dict[str, Any]],
    ) -> None:
        from backend.App.integrations.infrastructure.cross_task_memory import (
            _LOCAL_EPISODES,
            _list_key,
            _redis,
        )

        local_bucket = _LOCAL_EPISODES.get(namespace, [])
        cluster_bodies = {str(ep.get("body") or "") for ep in cluster}
        for ep in local_bucket:
            if str(ep.get("body") or "") in cluster_bodies:
                ep["consolidated"] = True

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

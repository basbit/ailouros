"""DreamPass — embedding-based memory consolidation (infrastructure layer).

Clusters cross-task episodes by their stored embedding vectors using pure-Python
k-means, then synthesises a "super-episode" per cluster and stores it via
``pattern_memory.store_consolidated_pattern``.

Toggle: ``SWARM_DREAM_PASS_ENABLED=1`` (default OFF).  No behaviour change
unless the env var is explicitly set.

Env vars
--------
SWARM_DREAM_PASS_ENABLED       — master toggle; default "0" (disabled).
SWARM_DREAM_PASS_MIN_CLUSTER   — minimum episodes per accepted cluster (default 3).
SWARM_DREAM_PASS_MAX_CLUSTERS  — k upper-bound for k-means (default 10).
SWARM_DREAM_PASS_KMEANS_ITERS  — max k-means iterations (default 30).
SWARM_DREAM_PASS_EPISODE_LIMIT — max episodes loaded per run (default 200).
"""

from __future__ import annotations

import logging
import math
import os
import random
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MIN_CLUSTER_SIZE_DEFAULT = int(os.getenv("SWARM_DREAM_PASS_MIN_CLUSTER", "3"))
_MAX_CLUSTERS_DEFAULT = int(os.getenv("SWARM_DREAM_PASS_MAX_CLUSTERS", "10"))
_KMEANS_ITERS = int(os.getenv("SWARM_DREAM_PASS_KMEANS_ITERS", "30"))
_EPISODE_LIMIT = int(os.getenv("SWARM_DREAM_PASS_EPISODE_LIMIT", "200"))


# ---------------------------------------------------------------------------
# Feature-flag
# ---------------------------------------------------------------------------


def _dream_pass_enabled() -> bool:
    return os.getenv("SWARM_DREAM_PASS_ENABLED", "0").strip() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Pure-Python k-means
# ---------------------------------------------------------------------------


def _vec_add(a: list[float], b: list[float]) -> list[float]:
    return [x + y for x, y in zip(a, b)]


def _vec_scale(a: list[float], s: float) -> list[float]:
    return [x * s for x in a]


def _l2_dist_sq(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b))


def _k_means_cluster(
    embeddings: list[list[float]],
    k: int,
) -> list[int]:
    """Pure-Python k-means.  Returns a cluster label (0..k-1) per embedding.

    Args:
        embeddings: List of equal-length float vectors.
        k: Number of clusters.  Clamped to ``len(embeddings)`` if too large.

    Returns:
        List of integer labels, same length as *embeddings*.
        All zeros on degenerate inputs (empty list or k <= 0).
    """
    n = len(embeddings)
    if n == 0 or k <= 0:
        return []
    k = min(k, n)
    if k == 1:
        return [0] * n

    dim = len(embeddings[0])
    if dim == 0:
        return [0] * n

    # Kmeans++ style initialisation — spread initial centroids
    rng = random.Random(42)
    chosen_indices: list[int] = [rng.randrange(n)]
    while len(chosen_indices) < k:
        # Compute squared distance from each point to nearest chosen centroid
        dists: list[float] = []
        for i in range(n):
            min_d = min(_l2_dist_sq(embeddings[i], embeddings[c]) for c in chosen_indices)
            dists.append(min_d)
        total = sum(dists)
        if total == 0.0:
            break
        # Weighted random pick
        threshold = rng.random() * total
        cumulative = 0.0
        pick = chosen_indices[-1]
        for i, d in enumerate(dists):
            cumulative += d
            if cumulative >= threshold:
                pick = i
                break
        if pick not in chosen_indices:
            chosen_indices.append(pick)

    centroids: list[list[float]] = [list(embeddings[i]) for i in chosen_indices]
    actual_k = len(centroids)
    labels = [0] * n

    for _ in range(_KMEANS_ITERS):
        # Assignment step
        new_labels = [0] * n
        for i in range(n):
            best = 0
            best_d = _l2_dist_sq(embeddings[i], centroids[0])
            for c_idx in range(1, actual_k):
                d = _l2_dist_sq(embeddings[i], centroids[c_idx])
                if d < best_d:
                    best_d = d
                    best = c_idx
            new_labels[i] = best

        # Update step
        sums: list[list[float]] = [[0.0] * dim for _ in range(actual_k)]
        counts: list[int] = [0] * actual_k
        for i, label in enumerate(new_labels):
            sums[label] = _vec_add(sums[label], embeddings[i])
            counts[label] += 1

        converged = True
        for c_idx in range(actual_k):
            if counts[c_idx] > 0:
                new_centroid = _vec_scale(sums[c_idx], 1.0 / counts[c_idx])
                if _l2_dist_sq(new_centroid, centroids[c_idx]) > 1e-10:
                    converged = False
                centroids[c_idx] = new_centroid

        labels = new_labels
        if converged:
            break

    return labels


# ---------------------------------------------------------------------------
# Token-overlap fallback grouping (for episodes without embeddings)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", text.lower()) if len(t) >= 3]


def _token_overlap(a: str, b: str) -> float:
    ta = set(_tokenize(a))
    tb = set(_tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / math.sqrt(len(ta) * len(tb))


def _token_cluster(
    episodes: list[dict[str, Any]],
    *,
    threshold: float = 0.25,
) -> list[int]:
    """Greedy single-linkage token-overlap clustering for episodes without embeddings."""
    n = len(episodes)
    labels = list(range(n))
    bodies = [str(ep.get("body") or "") for ep in episodes]

    for i in range(n):
        for j in range(i + 1, n):
            if _token_overlap(bodies[i], bodies[j]) >= threshold:
                old_id = labels[j]
                new_id = labels[i]
                if old_id != new_id:
                    for k in range(n):
                        if labels[k] == old_id:
                            labels[k] = new_id
    return labels


# ---------------------------------------------------------------------------
# Cluster summary builder
# ---------------------------------------------------------------------------


def _summarise_cluster(
    cluster: list[dict[str, Any]],
    *,
    cluster_idx: int,
) -> dict[str, Any]:
    """Build a synthetic super-episode representing a cluster.

    The body is a bullet list of the first sentence (or up to 200 chars) of
    each member episode, prefixed by step name and task_id.
    """
    bullets: list[str] = []
    steps: set[str] = set()
    task_ids: list[str] = []
    ts_values: list[float] = []

    for ep in cluster:
        step = str(ep.get("step") or "?")
        steps.add(step)
        tid = str(ep.get("task_id") or "")
        if tid:
            task_ids.append(tid)
        body = str(ep.get("body") or "").strip()
        # First sentence or first 200 chars
        first_sentence = re.split(r"[.!?\n]", body)[0].strip()
        snippet = (first_sentence or body)[:200]
        bullets.append(f"- [{step}/{tid[:8] or '?'}] {snippet}")
        ts_val = ep.get("ts")
        if isinstance(ts_val, (int, float)):
            ts_values.append(float(ts_val))

    step_list = sorted(steps)
    body = (
        f"Consolidated cluster {cluster_idx} ({len(cluster)} episodes, "
        f"steps: {', '.join(step_list)}):\n"
        + "\n".join(bullets)
    )
    super_episode: dict[str, Any] = {
        "body": body,
        "step": "dream_pass",
        "task_id": task_ids[0] if task_ids else "",
        "ts": max(ts_values) if ts_values else time.time(),
        "cluster_idx": cluster_idx,
        "cluster_size": len(cluster),
        "source_steps": step_list,
        "source_task_ids": task_ids[:10],
    }
    return super_episode


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def consolidate_episodes(
    namespace: str,
    *,
    min_cluster_size: int = _MIN_CLUSTER_SIZE_DEFAULT,
    max_clusters: int = _MAX_CLUSTERS_DEFAULT,
) -> list[dict]:
    """Read all episodes for *namespace*, cluster by embedding, return consolidated summaries.

    Episodes that carry an ``embedding`` list are clustered via k-means.
    Episodes without an embedding are grouped by token-overlap and appended
    as an additional cluster.

    Args:
        namespace: Cross-task memory namespace to read.
        min_cluster_size: Clusters smaller than this are skipped.
        max_clusters: Upper bound for k in k-means.

    Returns:
        List of synthetic super-episode dicts.  Empty when there are fewer
        episodes than *min_cluster_size*.
    """
    from backend.App.integrations.infrastructure.cross_task_memory import _load_episodes

    episodes = _load_episodes(namespace, limit=_EPISODE_LIMIT)
    if len(episodes) < min_cluster_size:
        logger.warning(
            "memory_consolidation: only %d episode(s) in namespace %r (min_cluster_size=%d) — skipping",
            len(episodes),
            namespace,
            min_cluster_size,
        )
        return []

    with_embedding = [ep for ep in episodes if isinstance(ep.get("embedding"), list) and ep["embedding"]]
    without_embedding = [ep for ep in episodes if ep not in with_embedding]

    super_episodes: list[dict] = []
    cluster_idx = 0

    # --- Embedding-based k-means clustering ---
    if with_embedding:
        vecs = [list(ep["embedding"]) for ep in with_embedding]
        k = max(1, min(max_clusters, len(with_embedding) // max(1, min_cluster_size)))
        labels = _k_means_cluster(vecs, k)

        groups: dict[int, list[dict[str, Any]]] = {}
        for i, label in enumerate(labels):
            groups.setdefault(label, []).append(with_embedding[i])

        for label, members in sorted(groups.items()):
            if len(members) < min_cluster_size:
                logger.warning(
                    "memory_consolidation: cluster %d has %d member(s) (< %d) — skipping",
                    label,
                    len(members),
                    min_cluster_size,
                )
                continue
            super_ep = _summarise_cluster(members, cluster_idx=cluster_idx)
            super_episodes.append(super_ep)
            cluster_idx += 1

    # --- Token-overlap fallback for episodes without embeddings ---
    if without_embedding:
        if len(without_embedding) >= min_cluster_size:
            fallback_labels = _token_cluster(without_embedding)
            fallback_groups: dict[int, list[dict[str, Any]]] = {}
            for i, label in enumerate(fallback_labels):
                fallback_groups.setdefault(label, []).append(without_embedding[i])
            for label, members in sorted(fallback_groups.items()):
                if len(members) < min_cluster_size:
                    logger.warning(
                        "memory_consolidation: fallback cluster %d has %d member(s) (< %d) — skipping",
                        label,
                        len(members),
                        min_cluster_size,
                    )
                    continue
                super_ep = _summarise_cluster(members, cluster_idx=cluster_idx)
                super_episodes.append(super_ep)
                cluster_idx += 1
        else:
            logger.warning(
                "memory_consolidation: %d episode(s) without embedding in namespace %r "
                "(< min_cluster_size=%d) — skipping fallback group",
                len(without_embedding),
                namespace,
                min_cluster_size,
            )

    return super_episodes


def dream_pass(namespace: str, state: Any = None) -> int:
    """Run a full DreamPass consolidation for *namespace*.

    Loads episodes, clusters them, and stores each cluster summary as a
    consolidated pattern in ``pattern_memory``.

    Toggle: only runs when ``SWARM_DREAM_PASS_ENABLED=1``.

    Args:
        namespace: Cross-task memory namespace to consolidate.
        state:     Optional pipeline state dict (used to resolve pattern_memory
                   path; ignored when ``None``).

    Returns:
        Number of cluster summaries created and stored.  Returns ``0`` when
        the feature toggle is off or when there are not enough episodes.
    """
    if not _dream_pass_enabled():
        logger.warning(
            "memory_consolidation.dream_pass: SWARM_DREAM_PASS_ENABLED is not set; "
            "no consolidation performed for namespace %r",
            namespace,
        )
        return 0

    from backend.App.integrations.infrastructure.pattern_memory import (
        store_consolidated_pattern,
        pattern_memory_path_for_state,
    )

    if state is not None:
        from collections.abc import Mapping as _Mapping
        if isinstance(state, _Mapping):
            pattern_path = pattern_memory_path_for_state(state)
        else:
            pattern_path = (Path.cwd() / ".swarm" / "pattern_memory.json").resolve()
    else:
        pattern_path = (Path.cwd() / ".swarm" / "pattern_memory.json").resolve()

    summaries = consolidate_episodes(namespace)
    if not summaries:
        return 0

    stored = 0
    for summary in summaries:
        cluster_idx = summary.get("cluster_idx", stored)
        steps = summary.get("source_steps") or ["unknown"]
        key = f"dream:{namespace}:cluster{cluster_idx}:{':'.join(steps[:3])}"
        provenance_ids = list(summary.get("source_task_ids") or [])
        body = str(summary.get("body") or "").strip()
        if not body:
            continue
        try:
            store_consolidated_pattern(
                pattern_key=key,
                value=body,
                provenance_ids=provenance_ids,
                path=pattern_path,
            )
            stored += 1
            logger.info(
                "memory_consolidation.dream_pass: stored cluster %d as pattern %r (provenance: %s)",
                cluster_idx,
                key,
                provenance_ids[:3],
            )
        except Exception as exc:
            logger.warning(
                "memory_consolidation.dream_pass: failed to store cluster %d: %s",
                cluster_idx,
                exc,
            )

    logger.info(
        "memory_consolidation.dream_pass: namespace=%r clusters_stored=%d",
        namespace,
        stored,
    )
    return stored

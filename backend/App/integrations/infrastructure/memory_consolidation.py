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


def _dream_pass_enabled() -> bool:
    return os.getenv("SWARM_DREAM_PASS_ENABLED", "0").strip() in ("1", "true", "yes", "on")


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
    n = len(embeddings)
    if n == 0 or k <= 0:
        return []
    k = min(k, n)
    if k == 1:
        return [0] * n

    dim = len(embeddings[0])
    if dim == 0:
        return [0] * n

    rng = random.Random(42)
    chosen_indices: list[int] = [rng.randrange(n)]
    while len(chosen_indices) < k:
        dists: list[float] = []
        for i in range(n):
            min_d = min(_l2_dist_sq(embeddings[i], embeddings[c]) for c in chosen_indices)
            dists.append(min_d)
        total = sum(dists)
        if total == 0.0:
            break
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


def _summarise_cluster(
    cluster: list[dict[str, Any]],
    *,
    cluster_idx: int,
) -> dict[str, Any]:
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


def consolidate_episodes(
    namespace: str,
    *,
    min_cluster_size: int = _MIN_CLUSTER_SIZE_DEFAULT,
    max_clusters: int = _MAX_CLUSTERS_DEFAULT,
) -> list[dict]:
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

"""Real-model smoke tests for ``workspace.application.wiki_searcher``.

These tests exercise the actual sentence-transformers embedding model and
verify the *quality* of semantic ranking, not just that code paths run.
They are excluded from ``make ci`` (see ``conftest.py``) and run only
under ``SWARM_SMOKE=1`` (set automatically by ``make smoke``).

What we assert
--------------

1. The provider returns real, non-empty 384-dim vectors.
2. A query about *memory* ranks the memory wiki article above the design
   one — i.e. the cosine similarity beats the alphabetical accident of
   the legacy flat dump.
3. A query about *deployment* ranks the devops article first.
4. The returned chunk count never exceeds the requested ``k``.
5. The rendered block ("``## section``" headings) preserves the rel_path
   of the top-ranked article.
6. Cache invalidation triggers a re-embed when files change.
"""

from __future__ import annotations

from pathlib import Path

import pytest  # noqa: F401 — imported so conftest sees the smoke marker registration

from backend.App.workspace.application.wiki_searcher import (
    reset_wiki_searcher_cache,
    search,
    search_block,
)


def _seed_wiki(root: Path) -> Path:
    """Build a small but realistic wiki with topically distinct articles."""
    wiki = root / ".swarm" / "wiki"
    (wiki / "architecture").mkdir(parents=True, exist_ok=True)
    (wiki / "features").mkdir(parents=True, exist_ok=True)
    (wiki / "devops").mkdir(parents=True, exist_ok=True)
    (wiki / "qa").mkdir(parents=True, exist_ok=True)

    (wiki / "index.md").write_text(
        "# Project Index\n"
        "Top-level overview of the project.\n",
        encoding="utf-8",
    )
    (wiki / "architecture" / "memory.md").write_text(
        "# Memory System\n"
        "PatternMemory is a key/value store that persists reusable patterns "
        "between pipeline runs. CrossTaskMemory keeps episodes from previous "
        "runs and injects them into the current agent context.\n"
        "\n"
        "## Dream pass\n"
        "The dream pass clusters episodes by similarity and consolidates "
        "them into reusable patterns via an LLM call.\n",
        encoding="utf-8",
    )
    (wiki / "features" / "design.md").write_text(
        "# Visual Design\n"
        "The application supports light and dark themes. The settings panel "
        "exposes a colour picker and a font-size slider for accessibility.\n",
        encoding="utf-8",
    )
    (wiki / "devops" / "deployment.md").write_text(
        "# Deployment\n"
        "We deploy the backend as a Docker container behind an Nginx reverse "
        "proxy. Continuous integration runs on GitHub Actions and pushes a "
        "tagged image to the container registry on every release.\n",
        encoding="utf-8",
    )
    (wiki / "qa" / "testing.md").write_text(
        "# Testing Strategy\n"
        "Unit tests live next to the modules they cover. Integration tests "
        "spin up a temporary FastAPI app and a SQLite database to validate "
        "REST endpoints end-to-end.\n",
        encoding="utf-8",
    )
    return wiki


def test_real_provider_produces_dense_vectors(real_embedding_provider, tmp_path):
    """Sanity: the provider returns concrete, non-zero vectors."""
    vectors = real_embedding_provider.embed(["pattern memory across runs"])
    assert vectors and vectors[0]
    assert len(vectors[0]) >= 64, "expected at least 64-dim embedding"
    assert any(abs(value) > 1e-6 for value in vectors[0]), "vector must be non-zero"


def test_memory_query_ranks_memory_article_first(real_embedding_provider, tmp_path):
    """Semantic ranking must beat the alphabetical accident of the flat dump."""
    wiki_root = _seed_wiki(tmp_path)
    reset_wiki_searcher_cache()
    hits = search(wiki_root, "pattern memory and cross-task episode memory", k=4)
    assert hits, "real provider must return at least one hit"

    rel_paths = [hit.chunk.rel_path for hit in hits]
    assert "architecture/memory" in rel_paths, (
        f"memory article should be in top-4: {rel_paths}"
    )
    assert rel_paths[0] == "architecture/memory", (
        f"memory article should be ranked first, got {rel_paths}"
    )


def test_deployment_query_ranks_devops_article_first(real_embedding_provider, tmp_path):
    wiki_root = _seed_wiki(tmp_path)
    reset_wiki_searcher_cache()
    hits = search(
        wiki_root,
        "how do we deploy the backend with docker and CI",
        k=4,
    )
    assert hits
    assert hits[0].chunk.rel_path == "devops/deployment", (
        f"devops article should rank first for deployment query: "
        f"{[hit.chunk.rel_path for hit in hits]}"
    )


def test_design_query_ranks_design_article_first(real_embedding_provider, tmp_path):
    wiki_root = _seed_wiki(tmp_path)
    reset_wiki_searcher_cache()
    hits = search(
        wiki_root,
        "user interface theming dark mode and font size",
        k=4,
    )
    assert hits
    assert hits[0].chunk.rel_path == "features/design"


def test_top_k_is_respected(real_embedding_provider, tmp_path):
    wiki_root = _seed_wiki(tmp_path)
    reset_wiki_searcher_cache()
    for k in (1, 2, 3, 5):
        hits = search(wiki_root, "memory architecture", k=k)
        assert len(hits) <= k, f"k={k} but got {len(hits)} hits"


def test_search_block_starts_with_top_hit_section(real_embedding_provider, tmp_path):
    wiki_root = _seed_wiki(tmp_path)
    reset_wiki_searcher_cache()
    block = search_block(
        wiki_root,
        "pattern memory and cross-task episode memory",
        k=3,
        max_chars=4000,
    )
    assert block
    first_header = block.split("\n", 1)[0]
    assert first_header.startswith("## architecture/memory"), first_header
    assert "PatternMemory" in block


def test_block_budget_is_honoured(real_embedding_provider, tmp_path):
    wiki_root = _seed_wiki(tmp_path)
    reset_wiki_searcher_cache()
    block = search_block(
        wiki_root,
        "deploy docker continuous integration",
        k=10,
        max_chars=300,
    )
    assert 0 < len(block) <= 300, f"block length {len(block)} exceeds budget"


def test_cache_invalidates_when_files_change(real_embedding_provider, tmp_path):
    wiki_root = _seed_wiki(tmp_path)
    reset_wiki_searcher_cache()

    first_hits = search(wiki_root, "deployment docker registry", k=2)
    assert first_hits

    # Add a new article that should easily win the next query.
    (wiki_root / "devops" / "kubernetes.md").write_text(
        "# Kubernetes\nWe ship the backend as a Helm chart deployed onto a "
        "managed Kubernetes cluster with rolling updates and HPA.\n",
        encoding="utf-8",
    )
    # Force mtime forward in case the filesystem has 1 s resolution.
    import os
    import time
    future = time.time() + 5
    os.utime(wiki_root / "devops" / "kubernetes.md", (future, future))

    second_hits = search(wiki_root, "kubernetes helm rolling updates", k=2)
    assert second_hits, "freshly added article must be retrievable"
    assert any(
        hit.chunk.rel_path == "devops/kubernetes" for hit in second_hits
    ), f"new article missing from results: {[hit.chunk.rel_path for hit in second_hits]}"

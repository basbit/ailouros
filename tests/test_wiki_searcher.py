"""Unit tests for ``workspace.application.wiki_searcher``.

These tests stub out the embedding provider so they're fast and
deterministic. The real-model behaviour (sentence-transformers /
Ollama embeddings) is exercised by ``tests/smoke/test_wiki_searcher_smoke.py``
under ``make smoke``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.App.integrations.infrastructure import embedding_service
from backend.App.workspace.application import wiki_searcher  # noqa: F401 — module-level patching target
from backend.App.workspace.application.wiki_searcher import (
    WikiChunk,
    _chunk_file,
    _coalesce_short,
    _cosine,
    _split_oversized,
    _split_paragraphs,
    _strip_frontmatter,
    _token_score,
    _tokens,
    reset_wiki_searcher_cache,
    search,
    search_block,
)


# ---------------------------------------------------------------------------
# Embedding stubs
# ---------------------------------------------------------------------------


class _StubProvider:
    """Deterministic provider — embeds tokens to one-hot 32-dim vectors."""

    name = "stub"
    dim = 32

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in _tokens(text):
                vec[hash(token) % self.dim] += 1.0
            # L2-normalise so cosine == dot.
            norm = sum(x * x for x in vec) ** 0.5
            if norm:
                vec = [x / norm for x in vec]
            out.append(vec)
        return out


class _NullCallProvider(_StubProvider):
    name = "null"
    dim = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[] for _ in texts]


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Each test gets a fresh provider singleton + searcher cache + clean env."""
    monkeypatch.delenv("SWARM_WIKI_SEARCH_ENABLED", raising=False)
    monkeypatch.delenv("SWARM_WIKI_SEARCH_TOPK", raising=False)
    monkeypatch.delenv("SWARM_WIKI_SEARCH_MAX_CHARS", raising=False)
    monkeypatch.delenv("SWARM_WIKI_SEARCH_MIN_CHUNK_CHARS", raising=False)
    monkeypatch.delenv("SWARM_WIKI_SEARCH_MAX_CHUNK_CHARS", raising=False)
    embedding_service.reset_embedding_provider()
    reset_wiki_searcher_cache()
    yield
    embedding_service.reset_embedding_provider()
    reset_wiki_searcher_cache()


def _install_provider(monkeypatch, provider: Any) -> None:
    """Force ``get_embedding_provider`` to return *provider*."""
    monkeypatch.setattr(embedding_service, "_provider_singleton", provider)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def test_strip_frontmatter_removes_yaml_block():
    text = "---\ntitle: foo\n---\nbody"
    assert _strip_frontmatter(text) == "body"


def test_strip_frontmatter_noop_when_missing():
    assert _strip_frontmatter("plain body") == "plain body"


def test_split_paragraphs_tracks_sections():
    text = (
        "preamble line\n"
        "\n"
        "# Section A\n"
        "alpha line\n"
        "\n"
        "still alpha\n"
        "\n"
        "## Sub\n"
        "beta line\n"
    )
    pairs = _split_paragraphs(text)
    sections = [s for s, _ in pairs]
    bodies = [b for _, b in pairs]
    assert sections == ["preamble", "Section A", "Section A", "Sub"]
    assert "alpha" in bodies[1]
    assert "still alpha" in bodies[2]
    assert "beta" in bodies[3]


def test_coalesce_short_merges_only_same_section():
    pairs = [
        ("A", "x"),
        ("A", "y"),
        ("B", "z"),
    ]
    merged = _coalesce_short(pairs, min_chars=80, max_chars=200)
    assert merged[0] == ("A", "x\n\ny")
    assert merged[1] == ("B", "z")


def test_split_oversized_breaks_long_paragraphs():
    long_text = ("Sentence one. " * 100).strip()
    out = _split_oversized([("S", long_text)], max_chars=200)
    assert len(out) > 1
    assert all(len(text) <= 220 for _, text in out)


def test_chunk_file_skips_frontmatter_and_returns_chunks():
    raw = "---\ntitle: x\n---\n# Heading\n" + ("long body paragraph " * 10)
    chunks = _chunk_file("a/b", raw)
    assert chunks
    assert all(chunk.rel_path == "a/b" for chunk in chunks)
    assert "title: x" not in chunks[0].text


# ---------------------------------------------------------------------------
# Token score & cosine
# ---------------------------------------------------------------------------


def test_token_score_combines_body_section_and_substring():
    chunk = WikiChunk(rel_path="x", section="Memory layer", text="pattern memory store")
    assert _token_score("pattern memory", chunk) > 0
    # Substring boost
    assert _token_score("pattern memory store", chunk) > _token_score("foo bar", chunk)


def test_cosine_handles_zero_and_mismatched_inputs():
    assert _cosine([], []) == 0.0
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Search behaviour with stub provider
# ---------------------------------------------------------------------------


def _write_wiki(tmp: Path) -> Path:
    root = tmp / ".swarm" / "wiki"
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text(
        "# Index\nProject overview about stuff.\n", encoding="utf-8"
    )
    (root / "architecture").mkdir(exist_ok=True)
    (root / "architecture" / "memory.md").write_text(
        "# Memory\n"
        "PatternMemory stores key value pairs.\n"
        "\n"
        "## Cross-task memory\n"
        "Episodes from previous runs are injected into agent context.\n",
        encoding="utf-8",
    )
    (root / "features").mkdir(exist_ok=True)
    (root / "features" / "design.md").write_text(
        "# Design\n"
        "User interface theming and dark mode toggle in the settings panel.\n",
        encoding="utf-8",
    )
    return root


def test_search_returns_relevant_chunk_with_stub_embeddings(tmp_path, monkeypatch):
    root = _write_wiki(tmp_path)
    _install_provider(monkeypatch, _StubProvider())
    hits = search(root, "pattern memory architecture", k=3)
    assert hits, "stub provider with overlapping tokens must produce hits"
    top = hits[0]
    assert "memory" in top.chunk.rel_path or "memory" in top.chunk.text.lower()


def test_search_falls_back_to_token_overlap_when_provider_is_null(tmp_path, monkeypatch):
    root = _write_wiki(tmp_path)
    _install_provider(monkeypatch, _NullCallProvider())
    hits = search(root, "pattern memory", k=2)
    assert hits, "token fallback should still surface the memory article"
    paths = [hit.chunk.rel_path for hit in hits]
    assert any("memory" in path for path in paths)


def test_search_returns_empty_for_empty_query(tmp_path, monkeypatch):
    root = _write_wiki(tmp_path)
    _install_provider(monkeypatch, _StubProvider())
    assert search(root, "", k=3) == []
    assert search(root, "   ", k=3) == []


def test_search_returns_empty_when_disabled(tmp_path, monkeypatch):
    root = _write_wiki(tmp_path)
    monkeypatch.setenv("SWARM_WIKI_SEARCH_ENABLED", "0")
    _install_provider(monkeypatch, _StubProvider())
    assert search(root, "pattern memory", k=3) == []


def test_search_returns_empty_when_wiki_missing(tmp_path, monkeypatch):
    _install_provider(monkeypatch, _StubProvider())
    assert search(tmp_path / "no_such", "anything", k=3) == []


def test_search_block_renders_section_headers_within_budget(tmp_path, monkeypatch):
    root = _write_wiki(tmp_path)
    _install_provider(monkeypatch, _StubProvider())
    block = search_block(root, "pattern memory cross task", k=3, max_chars=4000)
    assert block, "block must be non-empty when there are hits"
    assert "## " in block
    # Budget honoured — we set 4000 and never exceed it.
    assert len(block) <= 4000


def test_search_block_dedupes_repeated_section(tmp_path, monkeypatch):
    root = tmp_path / ".swarm" / "wiki"
    root.mkdir(parents=True)
    (root / "a.md").write_text(
        "# Same\nfirst\n\nsecond\n", encoding="utf-8"
    )
    _install_provider(monkeypatch, _StubProvider())
    block = search_block(root, "first second", k=5)
    # Only one (rel_path, section) header should appear even though there are
    # two paragraphs under "Same".
    assert block.count("## a — Same") == 1


def test_index_caches_until_files_change(tmp_path, monkeypatch):
    root = _write_wiki(tmp_path)
    provider = _StubProvider()
    _install_provider(monkeypatch, provider)
    search(root, "pattern memory", k=2)
    first_call_count = len(provider.calls)
    search(root, "another query", k=2)
    # Second search reuses the index: only the query is embedded, no new chunk
    # batch sent through the provider.
    chunk_call_sizes = [len(call) for call in provider.calls]
    assert first_call_count >= 2  # one chunk batch + at least one query
    # No additional bulk call (size > 1) on the second search beyond the first
    # bulk index build.
    bulk_calls = [size for size in chunk_call_sizes if size > 1]
    assert len(bulk_calls) == 1, f"index was rebuilt: bulk calls={bulk_calls}"


def test_index_rebuilds_when_file_mtime_changes(tmp_path, monkeypatch):
    root = _write_wiki(tmp_path)
    provider = _StubProvider()
    _install_provider(monkeypatch, provider)
    search(root, "pattern", k=2)
    bulk_before = sum(1 for call in provider.calls if len(call) > 1)
    # Touch file: make a real content change so mtime advances.
    target = root / "architecture" / "memory.md"
    new_text = target.read_text(encoding="utf-8") + "\n\n## Added section\nfresh content here.\n"
    target.write_text(new_text, encoding="utf-8")
    # Force mtime forward (some filesystems have 1s resolution).
    import os
    import time
    future = time.time() + 5
    os.utime(target, (future, future))
    search(root, "pattern", k=2)
    bulk_after = sum(1 for call in provider.calls if len(call) > 1)
    assert bulk_after == bulk_before + 1, "index should rebuild after file change"


def test_reset_cache_clears_specific_root(tmp_path, monkeypatch):
    root = _write_wiki(tmp_path)
    provider = _StubProvider()
    _install_provider(monkeypatch, provider)
    search(root, "pattern", k=2)
    bulk_before = sum(1 for call in provider.calls if len(call) > 1)
    reset_wiki_searcher_cache(root)
    search(root, "pattern", k=2)
    bulk_after = sum(1 for call in provider.calls if len(call) > 1)
    assert bulk_after == bulk_before + 1


# ---------------------------------------------------------------------------
# Integration with load_wiki_context (semantic ↔ flat-dump fallback)
# ---------------------------------------------------------------------------


def test_load_wiki_context_uses_searcher_when_query_provided(tmp_path, monkeypatch):
    _write_wiki(tmp_path)
    _install_provider(monkeypatch, _StubProvider())
    from backend.App.orchestration.application import wiki_context_loader
    block = wiki_context_loader.load_wiki_context(tmp_path, query="pattern memory")
    assert block
    assert "## " in block


def test_load_wiki_context_falls_back_to_flat_dump_without_query(tmp_path, monkeypatch):
    _write_wiki(tmp_path)
    _install_provider(monkeypatch, _StubProvider())
    from backend.App.orchestration.application import wiki_context_loader
    block = wiki_context_loader.load_wiki_context(tmp_path)
    # Flat dump always starts with "## index" because index.md is read first.
    assert block.startswith("## index")


def test_query_for_pipeline_step_picks_step_specific_source(tmp_path):
    from backend.App.orchestration.application import wiki_context_loader
    state = {
        "user_task": "build login flow",
        "pm_output": "PM analysis of authentication requirements",
        "ba_output": "BA acceptance criteria for login",
    }
    pm_query = wiki_context_loader.query_for_pipeline_step(state, "pm")
    arch_query = wiki_context_loader.query_for_pipeline_step(state, "architect")
    assert "build login flow" in pm_query
    assert "BA acceptance criteria" in arch_query
    # Step hint adds role-specific signal.
    assert "architecture" in arch_query.lower()


def test_query_for_pipeline_step_returns_empty_for_empty_state():
    from backend.App.orchestration.application import wiki_context_loader
    assert wiki_context_loader.query_for_pipeline_step({}, "pm") == ""
    assert wiki_context_loader.query_for_pipeline_step(None, "pm") == ""

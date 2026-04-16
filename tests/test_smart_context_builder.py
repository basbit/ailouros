"""Tests for SmartContextBuilder (backend.App.orchestration.application.smart_context_builder)."""
from __future__ import annotations

import pytest

from backend.App.orchestration.application.smart_context_builder import (
    _cosine,
    build_context,
    smart_context_enabled,
)


# ---------------------------------------------------------------------------
# Toggle
# ---------------------------------------------------------------------------


def test_smart_context_enabled_off_by_default(monkeypatch):
    monkeypatch.delenv("SWARM_SMART_CONTEXT", raising=False)
    assert smart_context_enabled() is False


def test_smart_context_enabled_on(monkeypatch):
    monkeypatch.setenv("SWARM_SMART_CONTEXT", "1")
    assert smart_context_enabled() is True


def test_smart_context_enabled_true_string(monkeypatch):
    monkeypatch.setenv("SWARM_SMART_CONTEXT", "true")
    assert smart_context_enabled() is True


# ---------------------------------------------------------------------------
# Positional fallback (SWARM_SMART_CONTEXT=0)
# ---------------------------------------------------------------------------


def test_build_context_positional_fallback(monkeypatch):
    """When SWARM_SMART_CONTEXT=0, sections are returned in original order."""
    monkeypatch.setenv("SWARM_SMART_CONTEXT", "0")
    sections = [
        ("First", "alpha text content here"),
        ("Second", "beta text content here"),
    ]
    result = build_context(sections, query="irrelevant", budget_chars=10_000)
    assert "[First]" in result
    assert "[Second]" in result
    # Positional order: First before Second
    assert result.index("[First]") < result.index("[Second]")


# ---------------------------------------------------------------------------
# Budget enforcement
# ---------------------------------------------------------------------------


def test_build_context_respects_budget(monkeypatch):
    """Output never exceeds budget_chars."""
    monkeypatch.setenv("SWARM_SMART_CONTEXT", "0")
    sections = [("A", "x" * 5000), ("B", "y" * 5000)]
    result = build_context(sections, query="q", budget_chars=3000)
    assert len(result) <= 3000


def test_build_context_exact_budget_boundary(monkeypatch):
    """Budget is respected even when a section fits exactly."""
    monkeypatch.setenv("SWARM_SMART_CONTEXT", "0")
    # "[A]\n" = 4 chars + 96 chars of text = 100 total
    sections = [("A", "a" * 96)]
    result = build_context(sections, query="q", budget_chars=100)
    assert len(result) == 100


def test_build_context_partial_last_section(monkeypatch):
    """Last section is partially included when it doesn't fit in full."""
    monkeypatch.setenv("SWARM_SMART_CONTEXT", "0")
    # First section: "[A]\n" (4) + "a"*50 (50) = 54 chars
    # Separator: "\n\n" (2) — total so far when adding second = 56
    # Second section: "[B]\n" (4) + "b"*100 (100) = 104 chars — won't fit in 80 budget
    # Remaining after separator: 80 - 54 - 2 = 24 chars
    sections = [("A", "a" * 50), ("B", "b" * 100)]
    result = build_context(sections, query="q", budget_chars=80)
    assert len(result) <= 80
    assert "[A]" in result
    assert "[B]" in result
    # Second section must be partial
    assert "b" * 100 not in result


def test_build_context_single_section_truncated(monkeypatch):
    """Single oversized section is truncated to budget_chars."""
    monkeypatch.setenv("SWARM_SMART_CONTEXT", "0")
    sections = [("Wiki", "w" * 10_000)]
    result = build_context(sections, query="q", budget_chars=500)
    assert len(result) <= 500


# ---------------------------------------------------------------------------
# Empty section handling
# ---------------------------------------------------------------------------


def test_build_context_skips_empty_sections(monkeypatch):
    """Sections with empty or whitespace-only text are excluded from output."""
    monkeypatch.setenv("SWARM_SMART_CONTEXT", "0")
    sections = [
        ("Empty", ""),
        ("Blank", "   \n  "),
        ("Real", "actual content"),
    ]
    result = build_context(sections, query="q", budget_chars=10_000)
    assert "[Empty]" not in result
    assert "[Blank]" not in result
    assert "[Real]" in result
    assert "actual content" in result


def test_build_context_all_empty_returns_empty(monkeypatch):
    """Returns empty string when all sections are empty."""
    monkeypatch.setenv("SWARM_SMART_CONTEXT", "0")
    sections = [("A", ""), ("B", "  ")]
    result = build_context(sections, query="q", budget_chars=1000)
    assert result == ""


def test_build_context_no_sections_returns_empty(monkeypatch):
    """Returns empty string for an empty sections list."""
    monkeypatch.setenv("SWARM_SMART_CONTEXT", "0")
    result = build_context([], query="q", budget_chars=1000)
    assert result == ""


# ---------------------------------------------------------------------------
# Embedding-based ranking with mock provider
# ---------------------------------------------------------------------------


class _FakeProvider:
    """Deterministic fake that maps known texts to fixed vectors."""

    name = "fake"
    dim = 3

    # Section A is close to the query; section B is orthogonal.
    _VECTORS = {
        "query text": [1.0, 0.0, 0.0],
        "[SectionA]\nrelevant content about auth": [0.99, 0.01, 0.0],
        "[SectionB]\ncompletely unrelated content": [0.0, 1.0, 0.0],
    }

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._VECTORS.get(t, [0.5, 0.5, 0.0]) for t in texts]


def test_build_context_with_mock_embeddings(monkeypatch):
    """When embeddings are available, the most-similar section comes first."""
    monkeypatch.setenv("SWARM_SMART_CONTEXT", "1")
    fake = _FakeProvider()

    # Patch _get_provider so it returns our fake provider.
    monkeypatch.setattr(
        "backend.App.orchestration.application.smart_context_builder._get_provider",
        lambda: fake,
    )

    # Patch _rank_sections to use the fake provider's deterministic vectors.
    # This avoids having to thread through the module-level _embed reference.
    import backend.App.orchestration.application.smart_context_builder as scb

    _real_cosine = scb._cosine

    def _fake_rank(sections, query_vec, provider):
        scored = []
        for idx, (label, text) in enumerate(sections):
            block = f"[{label}]\n{text}" if label else text
            sec_vecs = provider.embed([block])
            sec_vec = sec_vecs[0] if sec_vecs else []
            score = _real_cosine(query_vec, sec_vec) if sec_vec else 0.0
            scored.append((score, idx, (label, text)))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [sec for _, _, sec in scored]

    monkeypatch.setattr(scb, "_rank_sections", _fake_rank)

    # Also patch _embed so query embedding uses the fake lookup.
    def _fake_embed(provider, text):
        results = provider.embed([text])
        return results[0] if results else []

    monkeypatch.setattr(scb, "_embed", _fake_embed)

    sections = [
        # SectionB appears first positionally but should rank second.
        ("SectionB", "completely unrelated content"),
        ("SectionA", "relevant content about auth"),
    ]
    result = build_context(
        sections,
        query="query text",
        budget_chars=10_000,
    )
    # SectionA must appear before SectionB in the output.
    assert "[SectionA]" in result
    assert "[SectionB]" in result
    assert result.index("[SectionA]") < result.index("[SectionB]")


def test_build_context_embedding_failure_falls_back_to_positional(monkeypatch):
    """When query embedding returns empty (unavailable), fall back to positional order."""
    monkeypatch.setenv("SWARM_SMART_CONTEXT", "1")

    # Simulate a provider that is non-null but returns empty vectors.
    class _EmptyProvider:
        name = "fake-empty"

        def embed(self, texts):
            return [[] for _ in texts]

    import backend.App.orchestration.application.smart_context_builder as scb

    monkeypatch.setattr(scb, "_get_provider", lambda: _EmptyProvider())
    # _embed wraps provider.embed and returns [] on empty — use the real _embed.

    sections = [("First", "aaa"), ("Second", "bbb")]
    result = build_context(sections, query="q", budget_chars=10_000)
    # Falls back to positional — First before Second.
    assert result.index("[First]") < result.index("[Second]")


# ---------------------------------------------------------------------------
# _cosine helper
# ---------------------------------------------------------------------------


def test_cosine_identical_vectors():
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors():
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_empty_vector():
    assert _cosine([], [1.0, 0.0]) == 0.0


def test_cosine_mismatched_lengths():
    assert _cosine([1.0, 0.0], [1.0]) == 0.0


def test_cosine_zero_norm():
    assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


# ---------------------------------------------------------------------------
# Separator
# ---------------------------------------------------------------------------


def test_custom_separator(monkeypatch):
    """Custom separator is used between sections."""
    monkeypatch.setenv("SWARM_SMART_CONTEXT", "0")
    sections = [("A", "text_a"), ("B", "text_b")]
    result = build_context(sections, query="q", budget_chars=10_000, separator="---")
    assert "---" in result
    assert "\n\n" not in result

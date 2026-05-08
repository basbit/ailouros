"""Тесты SQLite-backed wiki ANN индекса (drop-in замена inline TF-IDF)."""

from pathlib import Path

import pytest

from backend.App.integrations.infrastructure.wiki_ann_sqlite import (
    build_sqlite_index,
    documents_from_sqlite,
    index_stats,
    search_sqlite,
    update_sqlite_index,
)


@pytest.fixture
def wiki_root(tmp_path: Path) -> Path:
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "alpha.md").write_text(
        "# Alpha\n\nWorkspace evidence drives every gate decision.\n",
        encoding="utf-8",
    )
    (root / "beta.md").write_text(
        "# Beta\n\nBudget and quality checks live in scenarios.\n",
        encoding="utf-8",
    )
    nested = root / "architecture"
    nested.mkdir()
    (nested / "memory.md").write_text(
        "# Memory layer\n\nReasoning bank stores trajectories.\n",
        encoding="utf-8",
    )
    return root


def test_build_creates_index_with_all_documents(wiki_root: Path, tmp_path: Path) -> None:
    target = tmp_path / "ann.db"
    build_sqlite_index(wiki_root, target)
    stats = index_stats(target)
    assert stats["documents"] == 3
    assert stats["terms"] > 0


def test_search_returns_relevant_document_first(wiki_root: Path, tmp_path: Path) -> None:
    target = tmp_path / "ann.db"
    build_sqlite_index(wiki_root, target)
    results = search_sqlite(target, "reasoning bank trajectories", top_k=2)
    assert results
    assert results[0]["relative_path"].endswith("memory.md")
    assert results[0]["score"] > 0


def test_update_handles_added_modified_removed(wiki_root: Path, tmp_path: Path) -> None:
    target = tmp_path / "ann.db"
    build_sqlite_index(wiki_root, target)

    (wiki_root / "gamma.md").write_text(
        "# Gamma\n\nNew document added later.\n", encoding="utf-8",
    )
    (wiki_root / "alpha.md").write_text(
        "# Alpha v2\n\nUpdated body with extra evidence keywords.\n",
        encoding="utf-8",
    )
    (wiki_root / "beta.md").unlink()

    diff = update_sqlite_index(wiki_root, target)
    assert diff == {"added": 1, "updated": 1, "removed": 1}

    stats = index_stats(target)
    assert stats["documents"] == 3

    docs = {doc.relative_path for doc in documents_from_sqlite(target)}
    assert "gamma.md" in docs
    assert "beta.md" not in docs


def test_search_empty_query_returns_nothing(wiki_root: Path, tmp_path: Path) -> None:
    target = tmp_path / "ann.db"
    build_sqlite_index(wiki_root, target)
    assert search_sqlite(target, "   ") == []


def test_search_missing_database_returns_nothing(tmp_path: Path) -> None:
    assert search_sqlite(tmp_path / "missing.db", "anything") == []

"""Tests for wiki_curator.audit_wiki and wiki_curator.curate_wiki."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.App.workspace.application.wiki_curator import (
    PIPELINE_STEPS,
    CuratorReport,
    audit_wiki,
    curate_wiki,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_article(
    wiki_root: Path,
    rel_path: str,
    *,
    tags: list[str] | None = None,
    body: str = "Some content here to satisfy the thin-article check.\n" * 4,
    links: list[str] | None = None,
) -> Path:
    """Write a minimal article to wiki_root/<rel_path>.md."""
    tags_str = ", ".join(f'"{t}"' for t in (tags or []))
    links_str = ", ".join(f'"{lnk}"' for lnk in (links or []))
    front = (
        f"---\n"
        f"title: {rel_path.split('/')[-1].replace('-', ' ').title()}\n"
        f"tags: [{tags_str}]\n"
        f"links: [{links_str}]\n"
        f"---\n\n"
    )
    path = wiki_root / f"{rel_path}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(front + body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# audit_wiki — edge cases
# ---------------------------------------------------------------------------


def test_audit_empty_dir_returns_empty_lists(tmp_path: Path) -> None:
    """audit_wiki on an empty directory must not crash and has no articles."""
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()

    report = audit_wiki(wiki_root)

    assert isinstance(report, CuratorReport)
    assert set(report.uncovered_steps) == set(PIPELINE_STEPS)
    assert report.stale_articles == []
    assert report.orphan_articles == []
    assert report.broken_links == []
    assert report.thin_articles == []


def test_audit_nonexistent_dir_returns_all_steps_uncovered(tmp_path: Path) -> None:
    """audit_wiki on a non-existent directory returns all steps as uncovered."""
    wiki_root = tmp_path / "no_such_dir"

    report = audit_wiki(wiki_root)

    assert isinstance(report, CuratorReport)
    assert set(report.uncovered_steps) == set(PIPELINE_STEPS)


def test_audit_uncovered_step_reported(tmp_path: Path) -> None:
    """An article covering 'dev' must remove 'dev' from uncovered_steps."""
    wiki_root = tmp_path / "wiki"
    # Write articles for all steps EXCEPT 'pm'
    for step in PIPELINE_STEPS:
        if step != "pm":
            _write_article(wiki_root, f"development/{step}", tags=[step])

    report = audit_wiki(wiki_root)

    assert "pm" in report.uncovered_steps
    # All other steps should be covered
    remaining = [s for s in report.uncovered_steps if s != "pm"]
    assert remaining == [], f"Unexpected uncovered steps: {remaining}"


def test_audit_broken_link_detected(tmp_path: Path) -> None:
    """An article referencing a non-existent article is flagged as broken."""
    wiki_root = tmp_path / "wiki"
    _write_article(wiki_root, "architecture/pipeline", links=["missing/article"])

    report = audit_wiki(wiki_root)

    assert any("missing/article" in bl for bl in report.broken_links), (
        f"Expected broken link not found in {report.broken_links}"
    )


def test_audit_valid_link_not_broken(tmp_path: Path) -> None:
    """A [[link]] referencing an existing article is not flagged."""
    wiki_root = tmp_path / "wiki"
    _write_article(wiki_root, "architecture/pipeline")
    _write_article(wiki_root, "index", links=["architecture/pipeline"])

    report = audit_wiki(wiki_root)

    assert not any("architecture/pipeline" in bl for bl in report.broken_links)


def test_audit_thin_article_detected(tmp_path: Path) -> None:
    """An article with less than 100 body chars is flagged as thin."""
    wiki_root = tmp_path / "wiki"
    _write_article(wiki_root, "features/tiny", body="Short.\n")

    report = audit_wiki(wiki_root)

    assert "features/tiny" in report.thin_articles


def test_audit_thick_article_not_thin(tmp_path: Path) -> None:
    """An article with >= 100 body chars is not flagged as thin."""
    wiki_root = tmp_path / "wiki"
    _write_article(wiki_root, "features/big", body="x" * 200)

    report = audit_wiki(wiki_root)

    assert "features/big" not in report.thin_articles


def test_audit_orphan_article_detected(tmp_path: Path) -> None:
    """An article with no inbound links is flagged as an orphan."""
    wiki_root = tmp_path / "wiki"
    _write_article(wiki_root, "architecture/orphan")

    report = audit_wiki(wiki_root)

    assert "architecture/orphan" in report.orphan_articles


def test_audit_index_not_flagged_as_orphan(tmp_path: Path) -> None:
    """The index article is never flagged as an orphan."""
    wiki_root = tmp_path / "wiki"
    _write_article(wiki_root, "index", links=["architecture/pipeline"])
    _write_article(wiki_root, "architecture/pipeline")

    report = audit_wiki(wiki_root)

    assert "index" not in report.orphan_articles


def test_audit_stale_article_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Articles older than stale_days are flagged."""
    import time as _time

    wiki_root = tmp_path / "wiki"
    article = _write_article(wiki_root, "old/article")

    # Force the mtime to be 40 days ago
    old_time = _time.time() - 40 * 86400
    import os
    os.utime(article, (old_time, old_time))

    report = audit_wiki(wiki_root, stale_days=30)

    assert "old/article" in report.stale_articles


# ---------------------------------------------------------------------------
# curate_wiki
# ---------------------------------------------------------------------------


def test_curate_dry_run_makes_no_changes(tmp_path: Path) -> None:
    """curate_wiki with dry_run=True must not modify any files."""
    wiki_root = tmp_path / "wiki"
    article = _write_article(wiki_root, "features/stale", body="tiny")
    original = article.read_text(encoding="utf-8")

    curate_wiki(wiki_root, dry_run=True)

    assert article.read_text(encoding="utf-8") == original


def test_curate_removes_broken_links(tmp_path: Path) -> None:
    """curate_wiki with dry_run=False removes broken [[link]] refs."""
    wiki_root = tmp_path / "wiki"
    article_path = _write_article(
        wiki_root,
        "architecture/pipeline",
        links=[],
        body="See [[missing/gone]] for details. " + "x" * 200,
    )
    # Manually inject a body-level broken link (not in frontmatter)
    text = article_path.read_text(encoding="utf-8")
    text += "\n\nSee also [[another/missing]].\n"
    article_path.write_text(text, encoding="utf-8")

    report = curate_wiki(wiki_root, dry_run=False)

    updated = article_path.read_text(encoding="utf-8")
    assert "[[missing/gone]]" not in updated
    assert "[[another/missing]]" not in updated
    # Broken links should be gone after curation
    assert not any("missing" in bl for bl in report.broken_links)


def test_curate_stubs_thin_articles(tmp_path: Path) -> None:
    """curate_wiki with dry_run=False replaces thin article bodies with a placeholder."""
    wiki_root = tmp_path / "wiki"
    _write_article(wiki_root, "features/sparse", body="Too short.\n")

    curate_wiki(wiki_root, dry_run=False)

    text = (wiki_root / "features/sparse.md").read_text(encoding="utf-8")
    assert "Auto-curated" in text or "placeholder" in text

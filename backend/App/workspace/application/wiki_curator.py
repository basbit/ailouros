"""Wiki curator — audits the runtime wiki for coverage, staleness, and hygiene.

Public API:
    audit_wiki(wiki_root, *, stale_days=30) -> CuratorReport
    curate_wiki(wiki_root, *, dry_run=True) -> CuratorReport

Both functions are read-only when ``dry_run=True`` (the default for
``curate_wiki``).  ``audit_wiki`` is always read-only.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Pipeline steps that must each have a covering wiki article.
PIPELINE_STEPS: list[str] = [
    "pm",
    "ba",
    "architect",
    "spec_merge",
    "dev_lead",
    "dev",
    "qa",
    "review_dev",
    "review_qa",
]

_THIN_BODY_MIN_CHARS = 100

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# Matches standalone [[link]] references in article bodies.
_LINK_PATTERN_RE = re.compile(r"\[\[([^\]]+)\]\]")


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class CuratorReport:
    """Structured output of a wiki audit or curation run."""

    uncovered_steps: list[str] = field(default_factory=list)
    """Pipeline steps with no corresponding wiki article."""

    stale_articles: list[str] = field(default_factory=list)
    """Article rel-paths not modified within *stale_days* days."""

    orphan_articles: list[str] = field(default_factory=list)
    """Article rel-paths with no inbound [[link]] references."""

    broken_links: list[str] = field(default_factory=list)
    """``source_rel_path -> target_rel_path`` strings for unresolvable links."""

    thin_articles: list[str] = field(default_factory=list)
    """Article rel-paths whose body is shorter than _THIN_BODY_MIN_CHARS chars."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _scan_articles(wiki_root: Path) -> list[Path]:
    """Return all .md files under wiki_root, sorted for determinism."""
    return sorted(wiki_root.rglob("*.md"))


def _rel(wiki_root: Path, path: Path) -> str:
    """Relative POSIX path without .md extension — used as article identifier."""
    return path.relative_to(wiki_root).with_suffix("").as_posix()


def _parse_frontmatter_tags(text: str) -> list[str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return []
    for line in m.group(1).splitlines():
        if not line.startswith("tags:"):
            continue
        value = line[len("tags:"):].strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [v.strip().strip('"').strip("'") for v in inner.split(",") if v.strip()]
    return []


def _body_text(text: str) -> str:
    """Return article text with frontmatter stripped."""
    m = _FRONTMATTER_RE.match(text)
    return text[m.end():] if m else text


def _parse_frontmatter_links(text: str) -> list[str]:
    """Extract the ``links:`` list from YAML frontmatter."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return []
    for line in m.group(1).splitlines():
        if not line.startswith("links:"):
            continue
        value = line[len("links:"):].strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [v.strip().strip('"').strip("'") for v in inner.split(",") if v.strip()]
    return []


def _all_links(text: str) -> list[str]:
    """Return all outgoing links — frontmatter ``links:`` + body ``[[link]]`` refs."""
    return _parse_frontmatter_links(text) + _body_links(text)


def _body_links(text: str) -> list[str]:
    body = _body_text(text)
    return [t.strip() for t in _WIKI_LINK_RE.findall(body)]


def _covers_step(rel_path: str, tags: list[str], step_id: str) -> bool:
    """True when this article is associated with *step_id*."""
    # Direct filename match: e.g. "sessions/2026-04-dev-task" covers "dev"
    rel_lower = rel_path.lower()
    if step_id in rel_lower.replace("-", "_").split("/")[-1].split("_"):
        return True
    # Check tags list
    return step_id in [t.lower() for t in tags]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def audit_wiki(wiki_root: Path, *, stale_days: int = 30) -> CuratorReport:
    """Scan the wiki and return a structured report. Read-only.

    Args:
        wiki_root: Path to the wiki directory (e.g. ``.swarm/wiki``).
        stale_days: Articles not modified in this many days are flagged stale.

    Returns:
        :class:`CuratorReport` with zero or more items in each category.
        Returns an empty report (no items, no crash) when *wiki_root* is
        missing or contains no articles.
    """
    report = CuratorReport()

    if not wiki_root.is_dir():
        # Return empty report; the wiki simply doesn't exist yet.
        report.uncovered_steps = list(PIPELINE_STEPS)
        return report

    md_files = _scan_articles(wiki_root)
    if not md_files:
        report.uncovered_steps = list(PIPELINE_STEPS)
        return report

    # Build per-article metadata
    article_data: dict[str, dict] = {}
    for path in md_files:
        rel = _rel(wiki_root, path)
        try:
            text = path.read_text(encoding="utf-8")
            mtime = path.stat().st_mtime
        except OSError as exc:
            logger.warning("wiki_curator: skipping %s: %s", path, exc)
            continue
        tags = _parse_frontmatter_tags(text)
        body = _body_text(text)
        links = _all_links(text)
        article_data[rel] = {
            "tags": tags,
            "body_chars": len(body.strip()),
            "mtime": mtime,
            "links": links,
        }

    all_rels = set(article_data.keys())
    stale_threshold = time.time() - stale_days * 86400

    # --- uncovered_steps ---
    for step in PIPELINE_STEPS:
        covered = any(
            _covers_step(rel, data["tags"], step)
            for rel, data in article_data.items()
        )
        if not covered:
            report.uncovered_steps.append(step)

    # --- stale_articles ---
    for rel, data in sorted(article_data.items()):
        if data["mtime"] < stale_threshold:
            report.stale_articles.append(rel)

    # --- inbound link count → orphan_articles ---
    inbound: dict[str, int] = {rel: 0 for rel in all_rels}
    for data in article_data.values():
        for target in data["links"]:
            if target in inbound:
                inbound[target] += 1

    index_rel: Optional[str] = "index" if "index" in all_rels else None
    for rel in sorted(all_rels):
        if rel == index_rel:
            continue  # index article is never an orphan by convention
        if inbound.get(rel, 0) == 0:
            report.orphan_articles.append(rel)

    # --- broken_links ---
    for rel, data in sorted(article_data.items()):
        for target in data["links"]:
            if target not in all_rels:
                report.broken_links.append(f"{rel} -> {target}")

    # --- thin_articles ---
    for rel, data in sorted(article_data.items()):
        if data["body_chars"] < _THIN_BODY_MIN_CHARS:
            report.thin_articles.append(rel)

    return report


def curate_wiki(wiki_root: Path, *, dry_run: bool = True) -> CuratorReport:
    """Run audit + apply safe auto-fixes. Returns the post-fix report.

    Safe auto-fixes (applied only when ``dry_run=False``):
    - Remove broken [[link]] references from article bodies.
    - Stub thin articles (replace body with a placeholder note).

    Args:
        wiki_root: Path to the wiki directory.
        dry_run: When True (default), no files are written.

    Returns:
        :class:`CuratorReport` reflecting the state *after* fixes (or
        identical to :func:`audit_wiki` when *dry_run* is True).
    """
    if dry_run:
        return audit_wiki(wiki_root)

    if not wiki_root.is_dir():
        return audit_wiki(wiki_root)

    md_files = _scan_articles(wiki_root)

    # Build set of valid rel paths before any modifications
    all_rels: set[str] = set()
    for path in md_files:
        all_rels.add(_rel(wiki_root, path))

    for path in md_files:
        rel = _rel(wiki_root, path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("wiki_curator.curate: could not read %s: %s", path, exc)
            continue

        changed = False

        # Fix 1: remove broken [[link]] references
        def _remove_broken(match: re.Match) -> str:
            target = match.group(1).strip()
            if target not in all_rels:
                logger.info(
                    "wiki_curator.curate: removing broken link [[%s]] from %s",
                    target, rel,
                )
                return ""  # remove the [[broken]] reference
            return match.group(0)

        new_text = _LINK_PATTERN_RE.sub(_remove_broken, text)
        if new_text != text:
            changed = True
            text = new_text

        # Fix 2: stub thin articles
        body = _body_text(text)
        if len(body.strip()) < _THIN_BODY_MIN_CHARS:
            stub_notice = (
                "\n_Auto-curated: article body was too thin. "
                "Add details to replace this placeholder._\n"
            )
            fm_match = _FRONTMATTER_RE.match(text)
            if fm_match:
                frontmatter = text[: fm_match.end()]
                new_text = frontmatter + stub_notice
            else:
                new_text = stub_notice
            if new_text != text:
                changed = True
                text = new_text
                logger.info("wiki_curator.curate: stubbed thin article %s", rel)

        if changed:
            try:
                path.write_text(text, encoding="utf-8")
            except OSError as exc:
                logger.warning("wiki_curator.curate: could not write %s: %s", path, exc)

    return audit_wiki(wiki_root)

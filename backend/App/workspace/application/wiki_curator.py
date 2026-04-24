from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.App.shared.infrastructure.wiki_frontmatter import (
    extract_all_wiki_links,
    extract_body_text,
    parse_frontmatter_tags,
)

logger = logging.getLogger(__name__)

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

_LINK_PATTERN_RE = re.compile(r"\[\[([^\]]+)\]\]")


@dataclass
class CuratorReport:
    uncovered_steps: list[str] = field(default_factory=list)
    stale_articles: list[str] = field(default_factory=list)
    orphan_articles: list[str] = field(default_factory=list)
    broken_links: list[str] = field(default_factory=list)
    thin_articles: list[str] = field(default_factory=list)


def _scan_articles(wiki_root: Path) -> list[Path]:
    return sorted(wiki_root.rglob("*.md"))


def _rel(wiki_root: Path, path: Path) -> str:
    return path.relative_to(wiki_root).with_suffix("").as_posix()


def _covers_step(rel_path: str, tags: list[str], step_id: str) -> bool:
    rel_lower = rel_path.lower()
    if step_id in rel_lower.replace("-", "_").split("/")[-1].split("_"):
        return True
    return step_id in [tag.lower() for tag in tags]


def audit_wiki(wiki_root: Path, *, stale_days: int = 30) -> CuratorReport:
    report = CuratorReport()

    if not wiki_root.is_dir():
        report.uncovered_steps = list(PIPELINE_STEPS)
        return report

    md_files = _scan_articles(wiki_root)
    if not md_files:
        report.uncovered_steps = list(PIPELINE_STEPS)
        return report

    article_data: dict[str, dict] = {}
    for path in md_files:
        rel_path = _rel(wiki_root, path)
        try:
            text = path.read_text(encoding="utf-8")
            mtime = path.stat().st_mtime
        except OSError as exc:
            logger.warning("wiki_curator: skipping %s: %s", path, exc)
            continue
        tags = parse_frontmatter_tags(text)
        body = extract_body_text(text)
        links = extract_all_wiki_links(text)
        article_data[rel_path] = {
            "tags": tags,
            "body_chars": len(body.strip()),
            "mtime": mtime,
            "links": links,
        }

    all_rels = set(article_data.keys())
    stale_threshold = time.time() - stale_days * 86400

    for step in PIPELINE_STEPS:
        covered = any(
            _covers_step(rel_path, data["tags"], step)
            for rel_path, data in article_data.items()
        )
        if not covered:
            report.uncovered_steps.append(step)

    for rel_path, data in sorted(article_data.items()):
        if data["mtime"] < stale_threshold:
            report.stale_articles.append(rel_path)

    inbound: dict[str, int] = {rel_path: 0 for rel_path in all_rels}
    for data in article_data.values():
        for target in data["links"]:
            if target in inbound:
                inbound[target] += 1

    index_rel: Optional[str] = "index" if "index" in all_rels else None
    for rel_path in sorted(all_rels):
        if rel_path == index_rel:
            continue
        if inbound.get(rel_path, 0) == 0:
            report.orphan_articles.append(rel_path)

    for rel_path, data in sorted(article_data.items()):
        for target in data["links"]:
            if target not in all_rels:
                report.broken_links.append(f"{rel_path} -> {target}")

    for rel_path, data in sorted(article_data.items()):
        if data["body_chars"] < _THIN_BODY_MIN_CHARS:
            report.thin_articles.append(rel_path)

    return report


def curate_wiki(wiki_root: Path, *, dry_run: bool = True) -> CuratorReport:
    if dry_run:
        return audit_wiki(wiki_root)

    if not wiki_root.is_dir():
        return audit_wiki(wiki_root)

    md_files = _scan_articles(wiki_root)

    all_rels: set[str] = set()
    for path in md_files:
        all_rels.add(_rel(wiki_root, path))

    for path in md_files:
        rel_path = _rel(wiki_root, path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("wiki_curator.curate: could not read %s: %s", path, exc)
            continue

        changed = False

        def _remove_broken(match: re.Match) -> str:
            target = match.group(1).strip()
            if target not in all_rels:
                logger.info(
                    "wiki_curator.curate: removing broken link [[%s]] from %s",
                    target, rel_path,
                )
                return ""
            return match.group(0)

        new_text = _LINK_PATTERN_RE.sub(_remove_broken, text)
        if new_text != text:
            changed = True
            text = new_text

        body = extract_body_text(text)
        if len(body.strip()) < _THIN_BODY_MIN_CHARS:
            stub_notice = (
                "\n_Auto-curated: article body was too thin. "
                "Add details to replace this placeholder._\n"
            )
            from backend.App.shared.infrastructure.wiki_frontmatter import _FRONTMATTER_RE
            frontmatter_match = _FRONTMATTER_RE.match(text)
            if frontmatter_match:
                frontmatter_text = text[: frontmatter_match.end()]
                new_text = frontmatter_text + stub_notice
            else:
                new_text = stub_notice
            if new_text != text:
                changed = True
                text = new_text
                logger.info("wiki_curator.curate: stubbed thin article %s", rel_path)

        if changed:
            try:
                path.write_text(text, encoding="utf-8")
            except OSError as exc:
                logger.warning("wiki_curator.curate: could not write %s: %s", path, exc)

    return audit_wiki(wiki_root)

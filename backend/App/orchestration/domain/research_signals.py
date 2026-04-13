"""Detect when a pipeline step output signals that external research is needed."""
from __future__ import annotations

import re

_URL_PATTERN = re.compile(r"https?://[^\s\)\"'<>]+")
_RESEARCH_PHRASES = (
    "need to research",
    "requires research",
    "unknown api",
    "external source",
    "external website",
    "web scraping",
    "scrape",
    "html selector",
    "css selector",
    "parse website",
    "crawl",
)


def extract_research_signals(text: str) -> dict[str, list[str]]:
    """Return {'urls': [...], 'phrases': [...]} if research signals are present.

    Returns empty lists if no signals found.
    """
    urls = list(dict.fromkeys(_URL_PATTERN.findall(text)))  # deduplicated, order-preserved
    phrases = [p for p in _RESEARCH_PHRASES if p in text.lower()]
    return {"urls": urls, "phrases": phrases}


def has_research_signals(text: str) -> bool:
    """Return True if the text contains any research signals (URLs or research phrases)."""
    signals = extract_research_signals(text)
    return bool(signals["urls"] or signals["phrases"])


def build_research_advisory(text: str) -> str:
    """Build a research advisory block to prepend to Dev Lead context.

    Returns empty string if no signals found.
    """
    signals = extract_research_signals(text)
    if not signals["urls"] and not signals["phrases"]:
        return ""
    lines = ["<research_advisory>"]
    if signals["urls"]:
        lines.append(f"  External URLs detected ({len(signals['urls'])}):")
        for url in signals["urls"][:10]:  # cap at 10
            lines.append(f"    - {url}")
        lines.append("  NOTE: These URLs have NOT been fetched. Dev Lead must mark tasks")
        lines.append("  that depend on these sources with research_required=true and")
        lines.append("  describe what concrete information (selectors, endpoints, auth)")
        lines.append("  the Dev will need to discover before implementing.")
    if signals["phrases"]:
        lines.append(f"  Research-signal phrases found: {', '.join(signals['phrases'])}")
    lines.append("</research_advisory>")
    return "\n".join(lines)

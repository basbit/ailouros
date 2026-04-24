from __future__ import annotations

import re
from typing import Any, Optional

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
_SOURCE_RESEARCH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bweb\s*search\b", re.IGNORECASE),
    re.compile(r"\bsearch\s+the\s+web\b", re.IGNORECASE),
    re.compile(r"\bbrowse(\s+the\s+web)?\b", re.IGNORECASE),
    re.compile(r"\binternet\b", re.IGNORECASE),
    re.compile(r"\bwebsite(s)?\b", re.IGNORECASE),
    re.compile(r"\bsite(s)?\b", re.IGNORECASE),
    re.compile(r"\bgoogle\b", re.IGNORECASE),
    re.compile(r"\bsearch\s+online\b", re.IGNORECASE),
    re.compile(r"\bweb\s+scrap(e|ing)\b", re.IGNORECASE),
    re.compile(r"\bscrap(e|ing)\b", re.IGNORECASE),
    re.compile(r"\bhtml\s+selector\b", re.IGNORECASE),
    re.compile(r"\bcss\s+selector\b", re.IGNORECASE),
    re.compile(r"\bparse\s+website\b", re.IGNORECASE),
    re.compile(r"\binstagram\b", re.IGNORECASE),
    re.compile(r"\bfacebook\b", re.IGNORECASE),
    re.compile(r"\btelegram\b", re.IGNORECASE),
    re.compile(r"\bexternal\s+source\b", re.IGNORECASE),
    re.compile(r"\bexternal\s+website\b", re.IGNORECASE),
    re.compile(r"\bнайд[ии]\b", re.IGNORECASE),
    re.compile(r"\bпоищ[иуа]\b", re.IGNORECASE),
    re.compile(r"\bинтернет\b", re.IGNORECASE),
    re.compile(r"\bсайт(ы|ов|ам|ах)?\b", re.IGNORECASE),
    re.compile(r"\bвеб\b", re.IGNORECASE),
    re.compile(r"\bпоиск\b", re.IGNORECASE),
    re.compile(r"\bпарс(ить|инг)\b", re.IGNORECASE),
)


def extract_research_signals(text: str) -> dict[str, list[str]]:
    urls = list(dict.fromkeys(_URL_PATTERN.findall(text)))  # deduplicated, order-preserved
    phrases = [p for p in _RESEARCH_PHRASES if p in text.lower()]
    return {"urls": urls, "phrases": phrases}


def has_research_signals(text: str) -> bool:
    signals = extract_research_signals(text)
    return bool(signals["urls"] or signals["phrases"])


def build_research_advisory(text: str) -> str:
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


def requires_source_research(
    text: str,
    agent_config: Optional[dict[str, Any]] = None,
) -> bool:
    if isinstance(agent_config, dict):
        swarm_cfg = agent_config.get("swarm")
        if isinstance(swarm_cfg, dict) and "require_source_research" in swarm_cfg:
            val = swarm_cfg.get("require_source_research")
            return str(val).strip().lower() not in ("", "0", "false", "no", "off")
    if _URL_PATTERN.search(text or ""):
        return True
    lowered = str(text or "").strip()
    return any(pattern.search(lowered) for pattern in _SOURCE_RESEARCH_PATTERNS)

from __future__ import annotations

import logging
import os
import re
from typing import Any
from html.parser import HTMLParser

logger = logging.getLogger(__name__)


def fetch_page_available() -> bool:
    try:
        import httpx as _httpx_check
        del _httpx_check
        return True
    except ImportError:
        return False


def _fetch_timeout() -> int:
    try:
        return int(os.getenv("SWARM_FETCH_PAGE_TIMEOUT", "10"))
    except ValueError:
        return 10


def _fetch_max_bytes() -> int:
    try:
        return int(os.getenv("SWARM_FETCH_PAGE_MAX_BYTES", "512000"))
    except ValueError:
        return 512000


class _HTMLTextExtractor(HTMLParser):
    _SKIP_TAGS = frozenset({
        "script", "style", "noscript", "svg", "head", "meta", "link",
    })

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag.lower() in ("br", "hr"):
            self._parts.append("\n")
        elif tag.lower() in (
            "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
            "li", "tr", "blockquote", "section", "article",
        ):
            self._parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _html_to_text(html: str) -> str:
    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


def fetch_page(url: str) -> str:
    import httpx

    url = (url or "").strip()
    if not url:
        return "ERROR: 'url' parameter is required"

    if not url.startswith(("http://", "https://")):
        return f"ERROR: Only HTTP/HTTPS URLs are supported, got: {url!r}"

    timeout = _fetch_timeout()
    max_bytes = _fetch_max_bytes()

    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            resp = client.get(
                url,
                headers={
                    "User-Agent": "AIlourOS-FetchPage/1.0",
                    "Accept": "text/html,application/xhtml+xml,text/plain,application/json",
                },
            )
            resp.raise_for_status()
    except httpx.TimeoutException:
        return f"ERROR: Request timed out after {timeout}s for URL: {url}"
    except httpx.HTTPStatusError as exc:
        return f"ERROR: HTTP {exc.response.status_code} for URL: {url}"
    except Exception as exc:
        return f"ERROR: Failed to fetch URL {url}: {exc}"

    content_type = resp.headers.get("content-type", "")
    body = resp.text[:max_bytes]

    if "text/html" in content_type or "xhtml" in content_type:
        text = _html_to_text(body)
    else:
        text = body

    if len(text) > max_bytes:
        text = text[:max_bytes] + "\n\n[…content truncated at max_bytes limit]"

    if not text.strip():
        return f"Page fetched but no readable text content found at: {url}"

    return f"Source: {url}\n\n{text}"


def fetch_page_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": (
                "Fetch a web page by URL and extract its text content. "
                "Use for reading documentation, articles, API references. "
                "Returns plain text extracted from the page."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch (must be http:// or https://)",
                    },
                },
                "required": ["url"],
            },
        },
    }

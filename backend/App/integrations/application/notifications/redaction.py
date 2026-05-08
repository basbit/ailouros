from __future__ import annotations

import re
from typing import Any

_API_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}", re.IGNORECASE),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"gh[ps_oru][A-Za-z0-9_]{20,}"),
    re.compile(r"xox[abpr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"),
    re.compile(r"Bearer [A-Za-z0-9_\-\.=]{16,}"),
    re.compile(r"api[_-]?key=[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
)
_ENV_KV_PATTERN = re.compile(
    r"(?P<key>(?:[A-Z][A-Z0-9_]{2,})|api[_-]?key|password|secret|token)=(?P<value>[^\s\"';]+)",
    re.IGNORECASE,
)
_PRIVATE_PATH_PATTERN = re.compile(r"(?:/Users/|/home/|C:\\\\Users\\\\)[A-Za-z0-9._-]+")
_LARGE_BLOCK_PATTERN = re.compile(r"```[\s\S]{200,}?```")


def redact_text(value: str, *, max_chars: int = 240) -> str:
    if not value:
        return ""
    cleaned = value
    for pattern in _API_KEY_PATTERNS:
        cleaned = pattern.sub("[REDACTED-SECRET]", cleaned)
    cleaned = _ENV_KV_PATTERN.sub(lambda match: f"{match.group('key')}=[REDACTED]", cleaned)
    cleaned = _PRIVATE_PATH_PATTERN.sub("[REDACTED-PATH]", cleaned)
    cleaned = _LARGE_BLOCK_PATTERN.sub("[REDACTED-CODE-BLOCK]", cleaned)
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 1] + "…"
    return cleaned


def redact_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            redacted[key] = redact_text(value)
        elif isinstance(value, dict):
            redacted[key] = redact_event_payload(value)
        elif isinstance(value, list):
            redacted[key] = [
                redact_text(item) if isinstance(item, str)
                else redact_event_payload(item) if isinstance(item, dict)
                else item
                for item in value
            ]
        else:
            redacted[key] = value
    return redacted


__all__ = ("redact_text", "redact_event_payload")

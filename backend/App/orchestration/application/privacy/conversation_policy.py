from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Optional

from backend.App.integrations.infrastructure.conversation_store import ConversationMessage

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s\-()]{7,}\d)(?!\d)")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,16}\b")


@dataclass(frozen=True)
class Policy:
    exclude_personal: bool = False
    redact_personal: bool = False
    drop_roles: frozenset[str] = frozenset()


def _contains_pii(text: str) -> bool:
    if not text:
        return False
    return bool(
        _EMAIL_RE.search(text)
        or _PHONE_RE.search(text)
        or _SSN_RE.search(text)
        or _CARD_RE.search(text)
    )


def _redact(text: str) -> str:
    redacted = _EMAIL_RE.sub("[redacted-email]", text)
    redacted = _PHONE_RE.sub("[redacted-phone]", redacted)
    redacted = _SSN_RE.sub("[redacted-ssn]", redacted)
    redacted = _CARD_RE.sub("[redacted-card]", redacted)
    return redacted


def apply_policy(
    message: ConversationMessage, policy: Policy
) -> Optional[ConversationMessage]:
    if message.role in policy.drop_roles:
        return None
    if not message.content.strip():
        return None
    if _contains_pii(message.content):
        if policy.exclude_personal:
            return None
        if policy.redact_personal:
            return replace(message, content=_redact(message.content))
    return message


__all__ = ["Policy", "apply_policy"]

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal

Severity = Literal["block", "warn"]


@dataclass(frozen=True)
class GuardianFinding:
    pattern: str
    severity: Severity
    message: str
    matched_excerpt: str


_RULES: tuple[tuple[str, re.Pattern[str], Severity, str], ...] = (
    (
        "override_ignore_previous",
        re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions?", re.IGNORECASE),
        "block",
        "Prompt injection detected: override attempt ('ignore previous instructions'). Input rejected.",
    ),
    (
        "override_disregard_system",
        re.compile(r"disregard\s+(?:the\s+)?system\s+prompt", re.IGNORECASE),
        "block",
        "Prompt injection detected: override attempt ('disregard system prompt'). Input rejected.",
    ),
    (
        "override_you_are_now",
        re.compile(r"you\s+are\s+now\s+(?:a|an)\s+\w", re.IGNORECASE),
        "block",
        "Prompt injection detected: override attempt ('you are now ...'). Input rejected.",
    ),
    (
        "override_new_system_message",
        re.compile(r"new\s+system\s+message", re.IGNORECASE),
        "block",
        "Prompt injection detected: override attempt ('new system message'). Input rejected.",
    ),
    (
        "role_confusion_unfiltered",
        re.compile(r"you\s+are\s+an\s+unfiltered\s+(?:AI|assistant|model)", re.IGNORECASE),
        "block",
        "Prompt injection detected: role-confusion attempt ('unfiltered AI'). Input rejected.",
    ),
    (
        "role_confusion_dan",
        re.compile(r"\bDAN\s+mode\b", re.IGNORECASE),
        "block",
        "Prompt injection detected: role-confusion attempt ('DAN mode'). Input rejected.",
    ),
    (
        "role_confusion_act_as_model",
        re.compile(r"act\s+as\s+a\s+different\s+(?:AI|model|assistant)", re.IGNORECASE),
        "block",
        "Prompt injection detected: role-confusion attempt ('act as a different model'). Input rejected.",
    ),
    (
        "tool_abuse_shell",
        re.compile(r"execute\s+the\s+following\s+shell", re.IGNORECASE),
        "block",
        "Prompt injection detected: tool-abuse attempt ('execute the following shell'). Input rejected.",
    ),
    (
        "tool_abuse_delete_all",
        re.compile(r"delete\s+all\s+files", re.IGNORECASE),
        "block",
        "Prompt injection detected: tool-abuse attempt ('delete all files'). Input rejected.",
    ),
    (
        "tool_abuse_rm_rf",
        re.compile(r"\brm\s+-rf\b"),
        "block",
        "Prompt injection detected: tool-abuse attempt ('rm -rf'). Input rejected.",
    ),
    (
        "egress_send_to_http",
        re.compile(r"send\s+to\s+https?://", re.IGNORECASE),
        "block",
        "Prompt injection detected: egress attempt ('send to http(s)://'). Input rejected.",
    ),
    (
        "egress_exfiltrate",
        re.compile(r"\bexfiltrate\b", re.IGNORECASE),
        "block",
        "Prompt injection detected: egress attempt ('exfiltrate'). Input rejected.",
    ),
    (
        "egress_post_to_webhook",
        re.compile(r"post\s+to\s+(?:a\s+)?webhook", re.IGNORECASE),
        "warn",
        "Suspicious egress hint ('post to webhook') — verify intent before proceeding.",
    ),
)

_EXCERPT_WINDOW = 80


def _excerpt(text: str, match: re.Match[str]) -> str:
    start = max(0, match.start() - 20)
    end = min(len(text), match.end() + 20)
    raw = text[start:end]
    if len(raw) > _EXCERPT_WINDOW:
        raw = raw[:_EXCERPT_WINDOW]
    return raw.replace("\n", " ").strip()


class PromptGuardian:
    def evaluate(self, text: str) -> tuple[GuardianFinding, ...]:
        if os.getenv("SWARM_PROMPT_GUARDIAN_DISABLED", "").strip() == "1":
            return ()
        if not text or not text.strip():
            return ()
        findings: list[GuardianFinding] = []
        for pattern_name, regex, severity, message in _RULES:
            m = regex.search(text)
            if m is not None:
                findings.append(
                    GuardianFinding(
                        pattern=pattern_name,
                        severity=severity,
                        message=message,
                        matched_excerpt=_excerpt(text, m),
                    )
                )
        return tuple(findings)


__all__ = ["GuardianFinding", "PromptGuardian", "Severity"]

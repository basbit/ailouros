"""Tests for clarify_parser — covers canonical and real-world LLM output variants."""
from backend.App.orchestration.application.nodes.clarify_parser import parse_clarify_questions


CANONICAL = """
NEEDS_CLARIFICATION

Questions for the user:
1. Which backend framework is used?
   Options: A) Symfony | B) Node.js | C) Django | Other
2. What is the trigger for parsing?
   Options: A) Daily cron | B) Real-time webhook | C) Manual | Other

Reason: Both decisions block decomposition.
"""

MARKDOWN_BOLD = """
NEEDS_CLARIFICATION

**Questions for the user:**
1. Which backend framework is used?
   **Options:** A) Symfony | B) Node.js | C) Django | Other
2. What is the trigger?
   **Options:** A) Daily cron | B) Webhook | Other

Reason: …
"""

PAREN_NUMBERING = """
NEEDS_CLARIFICATION

Questions for the user:
1) Which backend framework is used?
   Options: A) Symfony | B) Node.js | Other
2) What is the trigger?
   Options: A) Daily cron | B) Webhook | Other
"""

NO_REASON_SECTION = """
NEEDS_CLARIFICATION

Questions for the user:
1. Which backend framework is used?
   Options: A) Symfony | B) Node.js | Other
"""

RUSSIAN_HEADER_OPTIONS = """
NEEDS_CLARIFICATION

Questions for the user:
1. Какой фреймворк используется?
   Варианты: A) Symfony | B) Node.js | Other
"""

MISSING_OPTIONS = """
NEEDS_CLARIFICATION

Questions for the user:
1. Which framework?
2. What trigger?
   Options: A) Cron | B) Webhook | Other
"""


def test_canonical():
    qs = parse_clarify_questions(CANONICAL)
    assert len(qs) == 2
    assert qs[0].index == 1
    assert qs[0].text == "Which backend framework is used?"
    assert qs[0].options == ["Symfony", "Node.js", "Django", "Other"]
    assert qs[1].options == ["Daily cron", "Real-time webhook", "Manual", "Other"]


def test_markdown_bold():
    qs = parse_clarify_questions(MARKDOWN_BOLD)
    assert len(qs) == 2
    assert qs[0].options == ["Symfony", "Node.js", "Django", "Other"]
    assert qs[1].options == ["Daily cron", "Webhook", "Other"]


def test_paren_numbering():
    qs = parse_clarify_questions(PAREN_NUMBERING)
    assert len(qs) == 2
    assert qs[0].options == ["Symfony", "Node.js", "Other"]


def test_no_reason_section():
    qs = parse_clarify_questions(NO_REASON_SECTION)
    assert len(qs) == 1
    assert qs[0].options == ["Symfony", "Node.js", "Other"]


def test_russian_varianty():
    qs = parse_clarify_questions(RUSSIAN_HEADER_OPTIONS)
    assert len(qs) == 1
    assert qs[0].options == ["Symfony", "Node.js", "Other"]


def test_missing_options_graceful():
    qs = parse_clarify_questions(MISSING_OPTIONS)
    # Question 1 has no options — should be excluded by the caller's filter,
    # but parser still returns it (graceful degradation)
    assert len(qs) == 2
    assert qs[0].options == []
    assert qs[1].options == ["Cron", "Webhook", "Other"]


def test_no_needs_clarification():
    assert parse_clarify_questions("READY\n\nThe task is clear.") == []


def test_empty_string():
    assert parse_clarify_questions("") == []

"""Parser for structured clarify_input output (NEEDS_CLARIFICATION with Options)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ClarifyQuestion:
    index: int
    text: str
    options: list[str] = field(default_factory=list)
    # options is empty if the model did not provide Options line (graceful degradation)


def _strip_markdown_bold(text: str) -> str:
    """Remove **…** markdown bold markers, keep inner text."""
    return re.sub(r"\*\*([^*]+)\*\*", r"\1", text)


def parse_clarify_questions(text: str) -> list[ClarifyQuestion]:
    """Parse clarify_input output into a list of structured questions.

    Expected format (canonical):
        NEEDS_CLARIFICATION

        Questions for the user:
        1. Question text
           Options: A) opt1 | B) opt2 | Other

        Reason: …

    Also tolerates:
    - Markdown bold: **Questions for the user:**, **Options:**
    - Alternative numbering: ``1)`` in addition to ``1.``
    - Case variations and alternative option keywords: Options / Варианты / Choices
    - Missing or extra blank lines around the header
    - Missing Reason section

    Returns empty list if NEEDS_CLARIFICATION is absent or no questions found.
    Gracefully handles missing Options lines (returns question with empty options).
    """
    if "NEEDS_CLARIFICATION" not in text:
        return []

    # Strip markdown bold markers so all subsequent patterns work uniformly.
    clean = _strip_markdown_bold(text)

    # Locate the questions block.  The header may be followed immediately by a
    # newline (canonical) or by a space/colon variant.  The block ends at a
    # "Reason" line or end-of-string.
    block_match = re.search(
        r"Questions\s+for\s+the\s+user\s*:?\s*\n(.*?)(?:\nReason\s*:|\Z)",
        clean,
        re.DOTALL | re.IGNORECASE,
    )
    if not block_match:
        return []

    block = block_match.group(1)
    questions: list[ClarifyQuestion] = []

    # Match numbered items: "1." or "1)" followed by whitespace.
    q_pattern = re.compile(r"(\d+)[.)]\s+(.+?)(?=\n\d+[.)]|\Z)", re.DOTALL)

    for m in q_pattern.finditer(block):
        idx = int(m.group(1))
        body = m.group(2).strip()

        # Find options line — tolerates Options / Варианты / Choices, with or
        # without markdown bold, and optional colon.
        opt_match = re.search(
            r"(?:Options|Варианты|Choices)\s*:?\s*(.+)",
            body,
            re.IGNORECASE,
        )
        options: list[str] = []
        if opt_match:
            raw_opts = opt_match.group(1).split("\n")[0]  # only first line
            for part in raw_opts.split("|"):
                part = part.strip()
                # Strip leading letter prefix: "A) ", "B) ", "a) " etc.
                part = re.sub(r"^[A-Za-z]\)\s*", "", part)
                if part:
                    options.append(part)
            body = body[: opt_match.start()].strip()

        questions.append(ClarifyQuestion(index=idx, text=body, options=options))

    return questions

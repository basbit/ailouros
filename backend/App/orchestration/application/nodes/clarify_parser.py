from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ClarifyQuestion:
    index: int
    text: str
    options: list[str] = field(default_factory=list)


def _strip_markdown_bold(text: str) -> str:
    return re.sub(r"\*\*([^*]+)\*\*", r"\1", text)


def parse_clarify_questions(text: str) -> list[ClarifyQuestion]:
    if "NEEDS_CLARIFICATION" not in text:
        return []

    clean = _strip_markdown_bold(text)

    block_match = re.search(
        r"Questions\s+for\s+the\s+user\s*:?\s*\n(.*?)(?:\nReason\s*:|\Z)",
        clean,
        re.DOTALL | re.IGNORECASE,
    )
    if not block_match:
        return []

    block = block_match.group(1)
    questions: list[ClarifyQuestion] = []

    q_pattern = re.compile(r"(\d+)[.)]\s+(.+?)(?=\n\d+[.)]|\Z)", re.DOTALL)

    for m in q_pattern.finditer(block):
        idx = int(m.group(1))
        body = m.group(2).strip()

        opt_match = re.search(
            r"(?:Options|Варианты|Choices)\s*:?\s*(.+)",
            body,
            re.IGNORECASE,
        )
        options: list[str] = []
        if opt_match:
            raw_opts = opt_match.group(1).split("\n")[0]
            for part in raw_opts.split("|"):
                part = part.strip()
                part = re.sub(r"^[A-Za-z]\)\s*", "", part)
                if part:
                    options.append(part)
            body = body[: opt_match.start()].strip()

        questions.append(ClarifyQuestion(index=idx, text=body, options=options))

    return questions


from __future__ import annotations

import logging
import re
from typing import Optional

from backend.App.orchestration.infrastructure.agents.base_agent import (
    BaseAgent,
    load_prompt,
    resolve_agent_model,
    resolve_default_environment,
)

logger = logging.getLogger(__name__)

_ROLE = "WIKI_WRITER"

_SYSTEM_PROMPT_FALLBACK = """\
You are a technical documentation writer. Generate a concise, structured wiki
article in Markdown from the pipeline step output provided by the user.

Output format (required):
---
title: <one-line title>
tags: ["<tag1>", "<tag2>"]
links: []
---


<2-5 sentence summary of what happened or what this article covers>


- <bullet per significant change or finding>

Rules:
- Do NOT add any text before the opening `---` frontmatter delimiter.
- Do NOT include any section other than Summary and Key Changes.
- Preserve any block enclosed in <!-- manual -->...<!-- /manual --> verbatim.
- Use plain Markdown; no HTML except the preserved manual blocks.
- Keep the article under 600 words.
"""

_MANUAL_BLOCK_RE = re.compile(
    r"(<!--\s*manual\s*-->.*?<!--\s*/manual\s*-->)",
    re.DOTALL,
)


def _extract_manual_blocks(text: str) -> list[str]:
    return _MANUAL_BLOCK_RE.findall(text)


def _restore_manual_blocks(generated: str, blocks: list[str]) -> str:
    if not blocks:
        return generated
    preserved = "\n\n".join(blocks)
    return generated.rstrip() + "\n\n" + preserved


class WikiWriterAgent(BaseAgent):

    ROLE = _ROLE

    def __init__(
        self,
        *,
        system_prompt_path_override: Optional[str] = None,
        model_override: Optional[str] = None,
        environment_override: Optional[str] = None,
        remote_provider: Optional[str] = None,
        remote_api_key: Optional[str] = None,
        remote_base_url: Optional[str] = None,
        max_output_tokens: int = 0,
        system_prompt_extra: str = "",
    ) -> None:
        prompt_path = system_prompt_path_override or "specialized/specialized-wiki-writer.md"
        super().__init__(
            role=_ROLE,
            system_prompt=load_prompt(prompt_path, _SYSTEM_PROMPT_FALLBACK),
            model=model_override or resolve_agent_model(_ROLE),
            environment=environment_override or resolve_default_environment(),
            remote_provider=remote_provider,
            remote_api_key=remote_api_key,
            remote_base_url=remote_base_url,
            max_tokens=max_output_tokens,
            system_prompt_extra=system_prompt_extra,
        )

    def write_article(
        self,
        step_id: str,
        step_output: str,
        existing_content: str = "",
        max_chars: int = 2000,
    ) -> str:
        manual_blocks = _extract_manual_blocks(existing_content)

        truncated_output = step_output[:max_chars]
        if len(step_output) > max_chars:
            truncated_output += "\n…[truncated]"

        parts = [
            f"step_id: {step_id}",
            "",
            "## Pipeline step output",
            "",
            truncated_output,
        ]

        if existing_content.strip():
            existing_preview = existing_content[:1000]
            if len(existing_content) > 1000:
                existing_preview += "\n…[truncated]"
            parts += [
                "",
                "## Existing article content (update rather than replace)",
                "",
                existing_preview,
            ]

        user_input = "\n".join(parts)
        generated = self.run(user_input)
        return _restore_manual_blocks(generated, manual_blocks)

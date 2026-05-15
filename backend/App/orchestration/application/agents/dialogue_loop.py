from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.App.orchestration.application.context.delta_prompt import (
    build_dialogue_agent_delta_input,
    build_reviewer_history_compact,
    delta_prompting_enabled,
    store_artifact,
)

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ROUNDS = int(os.getenv("SWARM_DIALOGUE_MAX_ROUNDS", "3"))


@dataclass
class DialogueResult:
    final_output: str
    verdict: str
    rounds_used: int
    history: list[dict[str, str]] = field(default_factory=list)
    escalated: bool = False


class DialogueLoop:

    def __init__(self, max_rounds: int = _DEFAULT_MAX_ROUNDS) -> None:
        self.max_rounds = max_rounds

    def run(
        self,
        *,
        agent_a: Any,
        agent_b: Any,
        initial_input: str,
        extract_verdict_fn: Callable[[str], str],
        progress_queue: Any = None,
        step_label: str = "agent↔reviewer",
    ) -> DialogueResult:
        import json as _json
        import queue as _q

        def _emit(event_type: str, **kwargs: Any) -> None:
            if not isinstance(progress_queue, _q.Queue):
                return
            try:
                progress_queue.put(_json.dumps({
                    "_event_type": event_type,
                    "step": step_label,
                    **kwargs,
                }))
            except Exception:
                pass

        _delta = delta_prompting_enabled()
        if _delta:
            store_artifact(initial_input)

        history: list[dict[str, Any]] = []
        current_input = initial_input
        verdict = "NEEDS_WORK"
        final_output = ""

        for round_n in range(1, self.max_rounds + 1):
            _emit("dialogue_round", round=round_n, max_rounds=self.max_rounds,
                  status="in_progress",
                  message=f"[{step_label}] round {round_n}/{self.max_rounds}")
            logger.info(
                "DialogueLoop: %s round %d/%d starting",
                step_label, round_n, self.max_rounds,
            )

            a_output = agent_a.run(current_input, _progress_queue=progress_queue)
            logger.info(
                "DialogueLoop: %s round %d — agent_a produced %d chars",
                step_label, round_n, len(a_output),
            )

            if history and _delta:
                history_block = "\n\n" + build_reviewer_history_compact(history)
            elif history:
                history_block = (
                    "\n## Conversation history (previous rounds)\n"
                    + "\n---\n".join(
                        f"Round {i + 1} output:\n{h['output']}\n"
                        f"Round {i + 1} review:\n{h['review']}"
                        for i, h in enumerate(history)
                    )
                )
            else:
                history_block = ""

            reviewer_input = (
                f"## Original task\n{initial_input}\n\n"
                f"## Round {round_n} output from {getattr(agent_a, 'role', 'agent_a')}\n"
                f"{a_output}\n"
                + history_block
            )
            b_review = agent_b.run(reviewer_input, _progress_queue=progress_queue)
            verdict = extract_verdict_fn(b_review)
            final_output = a_output
            history.append({"round": round_n, "output": a_output, "review": b_review, "verdict": verdict})

            _emit("dialogue_round", round=round_n, status="done",
                  verdict=verdict,
                  message=f"[{step_label}] round {round_n} verdict: {verdict}")
            logger.info(
                "DialogueLoop: %s round %d verdict=%s",
                step_label, round_n, verdict,
            )

            if verdict == "OK":
                break

            if _delta:
                current_input = build_dialogue_agent_delta_input(
                    initial_input,
                    reviewer_feedback=b_review,
                    prev_output=a_output,
                    round_n=round_n + 1,
                )
            else:
                current_input = (
                    f"{initial_input}\n\n"
                    f"## Reviewer feedback (round {round_n}) — address ALL issues below\n"
                    f"{b_review}\n\n"
                    "## Your previous output that was rejected\n"
                    f"{a_output}\n"
                )
        else:
            logger.warning(
                "DialogueLoop: %s exhausted %d rounds without OK verdict — escalating",
                step_label, self.max_rounds,
            )
            _emit("dialogue_escalated",
                  rounds=self.max_rounds,
                  message=f"[{step_label}] exhausted {self.max_rounds} rounds — human review required")
            return DialogueResult(
                final_output=final_output,
                verdict="NEEDS_WORK",
                rounds_used=self.max_rounds,
                history=history,
                escalated=True,
            )

        return DialogueResult(
            final_output=final_output,
            verdict=verdict,
            rounds_used=round_n,
            history=history,
            escalated=False,
        )

"""DialogueLoop — multi-turn conversation between two agents (§12.2).

Replaces single-retry review with a proper N-round dialogue:
  Round 1: agent_a produces output → agent_b reviews → verdict
  Round 2: if NEEDS_WORK, agent_a receives b's feedback → produces v2 → b reviews
  …until OK or max_rounds exhausted → escalate to human gate.

H-2 (delta prompting): from round 2 onwards agent_a receives a compact delta
input (artifact reference for initial_input + reviewer feedback) instead of
the full initial_input re-embedded every round.  The reviewer history block
also uses compact artifact refs rather than full output+review texts.
Controlled by SWARM_DELTA_PROMPTING=1 (default ON).

Environment:
    SWARM_DIALOGUE_MAX_ROUNDS (int, default 3) — max back-and-forth rounds.
    SWARM_DELTA_PROMPTING (0|1, default 1) — enable H-2 delta prompting.

Usage::

    loop = DialogueLoop(max_rounds=3)
    result = loop.run(
        agent_a=dev_agent,
        agent_b=reviewer_agent,
        initial_input=user_task,
        progress_queue=state.get("_stream_progress_queue"),
        extract_verdict_fn=extract_verdict,
    )
    # result.final_output — last agent_a output that got OK (or best effort)
    # result.verdict — "OK" | "NEEDS_WORK"
    # result.rounds_used — number of rounds completed
    # result.history — list of (output, verdict) per round
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.App.orchestration.application.delta_prompt import (
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
    verdict: str  # "OK" | "NEEDS_WORK"
    rounds_used: int
    history: list[dict[str, str]] = field(default_factory=list)
    escalated: bool = False


class DialogueLoop:
    """Multi-turn conversation between agent_a (producer) and agent_b (reviewer).

    Each *round* is two LLM calls: one for agent_a, one for agent_b.
    The conversation history accumulates so both agents see the full exchange.
    SSE progress events are emitted into *progress_queue* (if provided) using
    the structured JSON format understood by :class:`StepStreamExecutor`.
    """

    def __init__(self, max_rounds: int = _DEFAULT_MAX_ROUNDS) -> None:
        self.max_rounds = max_rounds

    def run(
        self,
        *,
        agent_a: Any,          # BaseAgent subclass — the producer
        agent_b: Any,          # BaseAgent subclass — the reviewer
        initial_input: str,
        extract_verdict_fn: Callable[[str], str],
        progress_queue: Any = None,
        step_label: str = "agent↔reviewer",
    ) -> DialogueResult:
        """Run the dialogue loop.

        Args:
            agent_a: Producer agent — called with user_input on round 1,
                     with prior feedback on subsequent rounds.
            agent_b: Reviewer agent — called with agent_a's output each round.
            initial_input: The user task or spec that agent_a works from.
            extract_verdict_fn: Callable(str) → "OK" | "NEEDS_WORK".
            progress_queue: Optional queue.Queue for SSE progress events.
            step_label: Label shown in SSE events (e.g. "dev↔reviewer").

        Returns:
            :class:`DialogueResult` with the final output and metadata.
        """
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
        # Store initial_input as artifact on round 1 (used for compact refs later).
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

            # agent_a produces output
            a_output = agent_a.run(current_input, _progress_queue=progress_queue)
            logger.info(
                "DialogueLoop: %s round %d — agent_a produced %d chars",
                step_label, round_n, len(a_output),
            )

            # agent_b reviews
            # H-2: from round 2+ use compact artifact refs in history block
            # to avoid re-embedding full output + review texts (can be 100 KB+).
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

            # H-2: round 2+ uses compact delta input instead of re-embedding the full
            # initial_input (saves ~10-20 KB per NEEDS_WORK round).
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
            # Exhausted rounds without OK
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

"""Blackboard + BlackboardCoordinator — shared knowledge board (§12.7).

Implements the Blackboard pattern for star/mesh topologies:
- Agents post hypotheses with confidence scores
- Other agents can refine posted entries
- Coordinator decides which agent to activate next based on board state
- Contradiction detection triggers automatic debate

Usage::

    board = Blackboard()
    entry_id = board.post("ba", "auth_approach", "Use JWT with refresh tokens", confidence=0.8)
    board.refine(entry_id, "architect", "Agreed — add Redis session store for invalidation")
    unresolved = board.get_unresolved()
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Heuristic keyword pairs used to detect potential contradictions between board entries.
# These are intentionally minimal — architecture policy, not business logic.
# They serve only as rough triggers for debate (§12.7); no semantic truth is asserted.
_CONTRADICTION_PAIRS: list[tuple[set[str], set[str]]] = [
    ({"jwt", "token"}, {"session", "cookie"}),
    ({"sql", "relational"}, {"nosql", "mongo", "redis"}),
    ({"sync", "synchronous"}, {"async", "asynchronous"}),
    ({"rest", "http"}, {"graphql", "grpc"}),
]


@dataclass
class BoardEntry:
    id: str
    agent: str
    topic: str
    content: str
    confidence: float     # 0.0 – 1.0
    timestamp: str
    refinements: list[dict[str, str]] = field(default_factory=list)
    resolved: bool = False
    contradicted_by: list[str] = field(default_factory=list)  # entry IDs


class Blackboard:
    """Shared knowledge board where agents post and refine hypotheses.

    Stored as part of pipeline state via :meth:`to_state_dict` /
    :meth:`from_state_dict`.
    """

    def __init__(self) -> None:
        self._entries: list[BoardEntry] = []

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def post(
        self,
        agent: str,
        topic: str,
        content: str,
        confidence: float = 1.0,
    ) -> str:
        """Post a new hypothesis to the board.

        Args:
            agent: The posting agent's role.
            topic: Short label for grouping related entries (e.g. "auth_approach").
            content: The hypothesis or finding.
            confidence: 0.0 = speculation, 1.0 = certainty.

        Returns:
            The entry ID.
        """
        entry = BoardEntry(
            id=str(uuid.uuid4())[:8],
            agent=agent,
            topic=topic,
            content=content,
            confidence=max(0.0, min(1.0, confidence)),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._entries.append(entry)
        logger.debug("Blackboard.post: [%s] %s/%s conf=%.2f", entry.id, agent, topic, confidence)
        return entry.id

    def refine(self, entry_id: str, agent: str, new_content: str) -> bool:
        """Refine an existing entry.

        Returns True if the entry was found and updated.
        """
        for entry in self._entries:
            if entry.id == entry_id:
                entry.refinements.append({
                    "agent": agent,
                    "content": new_content,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                logger.debug("Blackboard.refine: [%s] refined by %s", entry_id, agent)
                return True
        return False

    def mark_resolved(self, entry_id: str) -> bool:
        for entry in self._entries:
            if entry.id == entry_id:
                entry.resolved = True
                return True
        return False

    def mark_contradiction(self, entry_id: str, contradicted_by_id: str) -> None:
        for entry in self._entries:
            if entry.id == entry_id:
                if contradicted_by_id not in entry.contradicted_by:
                    entry.contradicted_by.append(contradicted_by_id)
                return

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read_topic(self, topic: str) -> list[BoardEntry]:
        """Return all entries for *topic*, newest first."""
        return sorted(
            [e for e in self._entries if e.topic == topic],
            key=lambda e: e.timestamp,
            reverse=True,
        )

    def get_unresolved(self) -> list[BoardEntry]:
        """Return all unresolved entries."""
        return [e for e in self._entries if not e.resolved]

    def get_contradictions(self) -> list[BoardEntry]:
        """Return entries that have been flagged as contradicted."""
        return [e for e in self._entries if e.contradicted_by]

    def get_low_confidence(self, threshold: float = 0.5) -> list[BoardEntry]:
        """Return unresolved entries with confidence < *threshold*."""
        return [e for e in self._entries if not e.resolved and e.confidence < threshold]

    def to_summary(self) -> str:
        if not self._entries:
            return "(board is empty)"
        lines = ["## Blackboard\n"]
        by_topic: dict[str, list[BoardEntry]] = {}
        for e in self._entries:
            by_topic.setdefault(e.topic, []).append(e)
        for topic, entries in by_topic.items():
            lines.append(f"### {topic}")
            for e in entries:
                status = "✓ resolved" if e.resolved else f"conf={e.confidence:.0%}"
                if e.contradicted_by:
                    status += " ⚠ contradicted"
                lines.append(f"  [{e.id}] {e.agent}: {e.content[:120]} ({status})")
                for ref in e.refinements:
                    lines.append(f"    ↳ {ref['agent']}: {ref['content'][:80]}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_state_dict(self) -> dict[str, Any]:
        return {
            "entries": [
                {
                    "id": e.id,
                    "agent": e.agent,
                    "topic": e.topic,
                    "content": e.content,
                    "confidence": e.confidence,
                    "timestamp": e.timestamp,
                    "refinements": e.refinements,
                    "resolved": e.resolved,
                    "contradicted_by": e.contradicted_by,
                }
                for e in self._entries
            ]
        }

    @classmethod
    def from_state_dict(cls, data: dict[str, Any]) -> "Blackboard":
        board = cls()
        for raw in data.get("entries") or []:
            board._entries.append(BoardEntry(
                id=str(raw.get("id") or ""),
                agent=str(raw.get("agent") or ""),
                topic=str(raw.get("topic") or ""),
                content=str(raw.get("content") or ""),
                confidence=float(raw.get("confidence") or 1.0),
                timestamp=str(raw.get("timestamp") or ""),
                refinements=list(raw.get("refinements") or []),
                resolved=bool(raw.get("resolved", False)),
                contradicted_by=list(raw.get("contradicted_by") or []),
            ))
        return board


# ------------------------------------------------------------------
# BlackboardCoordinator
# ------------------------------------------------------------------

class BlackboardCoordinator:
    """Decides which agent to activate next based on blackboard state (§12.7).

    Rules:
    - Low confidence entry → activate reviewer for that topic
    - Contradiction detected → trigger debate (DialogueLoop)
    - All entries resolved → proceed to next pipeline phase
    """

    def __init__(self, board: Blackboard) -> None:
        self.board = board

    def next_action(self) -> dict[str, Any]:
        """Determine the next action based on current board state.

        Returns a dict with keys:
            action: "review" | "debate" | "proceed" | "wait"
            reason: explanation string
            targets: list of entry IDs or agent roles involved
        """
        contradictions = self.board.get_contradictions()
        if contradictions:
            return {
                "action": "debate",
                "reason": f"{len(contradictions)} contradictions detected",
                "targets": [e.id for e in contradictions],
            }
        low_conf = self.board.get_low_confidence()
        if low_conf:
            topics = list({e.topic for e in low_conf})
            return {
                "action": "review",
                "reason": f"Low-confidence entries on topics: {topics}",
                "targets": [e.id for e in low_conf],
            }
        unresolved = self.board.get_unresolved()
        if not unresolved:
            return {"action": "proceed", "reason": "All entries resolved", "targets": []}
        return {
            "action": "wait",
            "reason": f"{len(unresolved)} unresolved entries with acceptable confidence",
            "targets": [e.id for e in unresolved],
        }

    def detect_contradiction(self, entry_a_id: str, entry_b_id: str, similarity_threshold: float = 0.15) -> bool:
        """Return True and mark contradiction if two entries appear to contradict.

        Uses the module-level ``_CONTRADICTION_PAIRS`` keyword heuristic to detect
        mutually exclusive technology choices on the same topic.  These are heuristic
        triggers only — not semantic truth assertions.  A True result means the debate
        mechanism should be activated, not that the entries are provably wrong.
        """
        a = next((e for e in self.board._entries if e.id == entry_a_id), None)
        b = next((e for e in self.board._entries if e.id == entry_b_id), None)
        if not a or not b or a.topic != b.topic:
            return False

        a_lower = a.content.lower()
        b_lower = b.content.lower()
        for pos, neg in _CONTRADICTION_PAIRS:
            if any(w in a_lower for w in pos) and any(w in b_lower for w in neg):
                self.board.mark_contradiction(entry_a_id, entry_b_id)
                logger.info("Blackboard: contradiction detected between [%s] and [%s]", entry_a_id, entry_b_id)
                return True
            if any(w in b_lower for w in pos) and any(w in a_lower for w in neg):
                self.board.mark_contradiction(entry_b_id, entry_a_id)
                logger.info("Blackboard: contradiction detected between [%s] and [%s]", entry_b_id, entry_a_id)
                return True
        return False


# ------------------------------------------------------------------
# State integration
# ------------------------------------------------------------------

_BOARD_KEY = "_blackboard"


def get_blackboard(state: dict[str, Any]) -> Blackboard:
    """Get or create the blackboard from pipeline *state*."""
    board = state.get(_BOARD_KEY)
    if not isinstance(board, Blackboard):
        board = Blackboard()
        state[_BOARD_KEY] = board
    return board

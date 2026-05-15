from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from backend.App.shared.application.datetime_utils import utc_now_iso

logger = logging.getLogger(__name__)

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
    confidence: float
    timestamp: str
    refinements: list[dict[str, str]] = field(default_factory=list)
    resolved: bool = False
    contradicted_by: list[str] = field(default_factory=list)


class Blackboard:

    def __init__(self) -> None:
        self._entries: list[BoardEntry] = []

    def post(
        self,
        agent: str,
        topic: str,
        content: str,
        confidence: float = 1.0,
    ) -> str:
        entry = BoardEntry(
            id=str(uuid.uuid4())[:8],
            agent=agent,
            topic=topic,
            content=content,
            confidence=max(0.0, min(1.0, confidence)),
            timestamp=utc_now_iso(),
        )
        self._entries.append(entry)
        logger.debug("Blackboard.post: [%s] %s/%s conf=%.2f", entry.id, agent, topic, confidence)
        return entry.id

    def refine(self, entry_id: str, agent: str, new_content: str) -> bool:
        for entry in self._entries:
            if entry.id == entry_id:
                entry.refinements.append({
                    "agent": agent,
                    "content": new_content,
                    "timestamp": utc_now_iso(),
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

    def read_topic(self, topic: str) -> list[BoardEntry]:
        return sorted(
            [e for e in self._entries if e.topic == topic],
            key=lambda e: e.timestamp,
            reverse=True,
        )

    def get_unresolved(self) -> list[BoardEntry]:
        return [e for e in self._entries if not e.resolved]

    def get_contradictions(self) -> list[BoardEntry]:
        return [e for e in self._entries if e.contradicted_by]

    def get_low_confidence(self, threshold: float = 0.5) -> list[BoardEntry]:
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


class BlackboardCoordinator:

    def __init__(self, board: Blackboard) -> None:
        self.board = board

    def next_action(self) -> dict[str, Any]:
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


_BOARD_KEY = "_blackboard"


def get_blackboard(state: dict[str, Any]) -> Blackboard:
    board = state.get(_BOARD_KEY)
    if not isinstance(board, Blackboard):
        board = Blackboard()
        state[_BOARD_KEY] = board
    return board

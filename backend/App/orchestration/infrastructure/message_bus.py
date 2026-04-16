"""AgentMessageBus — pub/sub message bus for mesh topology agents (§12.6).

Enables peer-to-peer agent communication in mesh topology:
- Each agent has a mailbox
- Direct messages: dev → qa "can you verify this?"
- Broadcasts: "spec changed, all agents re-read"
- Messages stored in state for observability

Usage::

    bus = AgentMessageBus()
    bus.publish(from_agent="dev", to_agent="qa", message="Please verify src/app.py", msg_type="verify_request")
    bus.broadcast(from_agent="architect", message="Spec updated — please re-read architecture section")
    messages = bus.get_messages("qa")
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Message:
    id: str
    from_agent: str
    to_agent: str         # "__broadcast__" for broadcasts
    message: str
    msg_type: str
    timestamp: str
    read: bool = False


class AgentMessageBus:
    """In-process pub/sub message bus for inter-agent communication.

    Thread-safe for use within a single pipeline run (all agents share the
    same bus instance through pipeline state).
    """

    def __init__(self) -> None:
        self._messages: list[Message] = []

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish(
        self,
        from_agent: str,
        to_agent: str,
        message: str,
        msg_type: str = "message",
    ) -> str:
        """Send a direct message from *from_agent* to *to_agent*.

        Returns the message ID.
        """
        msg = Message(
            id=str(uuid.uuid4())[:8],
            from_agent=from_agent,
            to_agent=to_agent,
            message=message,
            msg_type=msg_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._messages.append(msg)
        logger.debug(
            "MessageBus: %s → %s [%s]: %s",
            from_agent, to_agent, msg_type, message[:100],
        )
        return msg.id

    def broadcast(self, from_agent: str, message: str, msg_type: str = "broadcast") -> str:
        """Broadcast a message to all agents.

        Returns the message ID.
        """
        return self.publish(
            from_agent=from_agent,
            to_agent="__broadcast__",
            message=message,
            msg_type=msg_type,
        )

    # ------------------------------------------------------------------
    # Receiving
    # ------------------------------------------------------------------

    def get_messages(self, agent_id: str, *, mark_read: bool = True) -> list[Message]:
        """Return all unread messages addressed to *agent_id* (including broadcasts).

        Args:
            agent_id: The receiving agent's role.
            mark_read: If True, mark returned messages as read.

        Returns:
            List of unread :class:`Message` objects.
        """
        results = [
            m for m in self._messages
            if not m.read and (m.to_agent == agent_id or m.to_agent == "__broadcast__")
        ]
        if mark_read:
            for m in results:
                m.read = True
        return results

    def get_all_messages(self) -> list[Message]:
        """Return all messages (for observability/logging)."""
        return list(self._messages)

    def subscribe(self, agent_id: str, callback: Any) -> None:
        """Register a callback for future messages (not used in current sync impl).

        Retained for API compatibility with future async implementations.
        """
        logger.debug("MessageBus.subscribe: %s registered (no-op in sync mode)", agent_id)

    def to_summary(self) -> str:
        """Return a human-readable summary of all messages."""
        if not self._messages:
            return "(no messages)"
        lines = ["## Message Bus Log\n"]
        for m in self._messages:
            read_marker = "✓" if m.read else "○"
            lines.append(
                f"  [{read_marker}] {m.timestamp[:19]} {m.from_agent} → {m.to_agent} "
                f"[{m.msg_type}]: {m.message[:80]}"
            )
        return "\n".join(lines)

    def to_state_dict(self) -> dict[str, Any]:
        return {
            "messages": [
                {
                    "id": m.id,
                    "from_agent": m.from_agent,
                    "to_agent": m.to_agent,
                    "message": m.message,
                    "msg_type": m.msg_type,
                    "timestamp": m.timestamp,
                    "read": m.read,
                }
                for m in self._messages
            ]
        }

    @classmethod
    def from_state_dict(cls, data: dict[str, Any]) -> "AgentMessageBus":
        bus = cls()
        for raw in data.get("messages") or []:
            bus._messages.append(Message(
                id=str(raw.get("id") or ""),
                from_agent=str(raw.get("from_agent") or ""),
                to_agent=str(raw.get("to_agent") or ""),
                message=str(raw.get("message") or ""),
                msg_type=str(raw.get("msg_type") or "message"),
                timestamp=str(raw.get("timestamp") or ""),
                read=bool(raw.get("read", False)),
            ))
        return bus


# ------------------------------------------------------------------
# State integration helpers
# ------------------------------------------------------------------

_BUS_KEY = "_message_bus"


def get_message_bus(state: dict[str, Any]) -> AgentMessageBus:
    """Get or create the message bus from pipeline *state*."""
    bus = state.get(_BUS_KEY)
    if not isinstance(bus, AgentMessageBus):
        bus = AgentMessageBus()
        state[_BUS_KEY] = bus
    return bus

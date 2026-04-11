"""Client-side event utilities for MCP.

ProvenanceEnvelope wraps events with client-assessed provenance metadata
for safe injection into LLM context. EventQueue provides priority-aware
buffering for events waiting to be processed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, ClassVar

from mcp.types import EventParams

__all__ = ["EventQueue", "ProvenanceEnvelope"]


@dataclass
class ProvenanceEnvelope:
    """Client-side provenance wrapper for events injected into LLM context.

    Clients generate this locally when formatting events for the LLM.
    The ``server_trust`` field MUST be client-assessed, never server-supplied.

    XML attribute order is normative per MCP Events Spec v2:
    ``server, topic, priority, event_id, trust, source``.
    """

    server: str
    server_trust: str  # Client-assessed trust tier (e.g., "trusted", "unknown")
    topic: str
    source: str | None = None
    event_id: str | None = None
    received_at: str | None = None  # ISO 8601, client-stamped

    priority: str = "normal"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for XML attributes, omitting None values.

        Attribute order matches the spec v2 normative order:
        ``server, topic, priority, event_id, trust, source``. The
        ``trust`` attribute is always emitted (server_trust is a
        REQUIRED client-assessed field).
        """
        d: dict[str, Any] = {
            "server": self.server,
            "topic": self.topic,
            "priority": self.priority,
        }
        if self.event_id is not None:
            d["event_id"] = self.event_id
        d["trust"] = self.server_trust
        if self.source is not None:
            d["source"] = self.source
        return d

    def to_xml(self, payload_text: str = "") -> str:
        """Format as XML element for LLM context injection.

        Produces the normative XML format per MCP Events Spec v2::

            <mcp:event server="NAME" topic="TOPIC" priority="PRIORITY"
                       event_id="ID" trust="LEVEL" source="SRC">
            ESCAPED_PAYLOAD
            </mcp:event>

        All attribute values are XML-escaped via quoteattr to prevent
        injection from attacker-controlled field values.
        """
        from xml.sax.saxutils import escape, quoteattr  # noqa: PLC0415

        attrs = " ".join(f"{k}={quoteattr(str(v))}" for k, v in self.to_dict().items())
        return f"<mcp:event {attrs}>\n{escape(payload_text)}\n</mcp:event>"

    @classmethod
    def from_event(
        cls,
        event: EventParams,
        *,
        server: str,
        server_trust: str,
    ) -> ProvenanceEnvelope:
        """Create an envelope from an EventParams notification.

        Extracts topic, source, event_id, and priority from the event and
        stamps ``received_at`` with the current UTC time. Events without an
        explicit ``priority`` default to ``"normal"``.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        return cls(
            server=server,
            server_trust=server_trust,
            topic=event.topic,
            source=event.source,
            event_id=event.eventId,
            received_at=datetime.now(timezone.utc).isoformat(),
            priority=event.priority or "normal",
        )


class EventQueue:
    """Priority-aware event buffer for client-side processing.

    Events are enqueued at the priority declared on ``EventParams.priority``.
    drain() returns events in priority order (urgent > high > normal > low).
    Events with no explicit priority are treated as ``"normal"``.
    """

    _PRIORITY_ORDER: ClassVar[dict[str, int]] = {
        "urgent": 0,
        "high": 1,
        "normal": 2,
        "low": 3,
    }

    def __init__(self) -> None:
        self._queues: dict[str, deque[EventParams]] = {p: deque() for p in self._PRIORITY_ORDER}

    def enqueue(self, event: EventParams) -> None:
        """Add an event to the appropriate priority queue.

        Priority is read directly from ``EventParams.priority``. Events
        without a priority default to ``"normal"``.
        """
        priority = self._resolve_priority(event)
        self._queues[priority].append(event)

    def drain(self, max_count: int | None = None) -> list[EventParams]:
        """Remove and return events in priority order.

        Args:
            max_count: Maximum events to return. None means drain all.

        Returns:
            Events ordered urgent -> high -> normal -> low.
        """
        result: list[EventParams] = []
        for priority in self._PRIORITY_ORDER:
            q = self._queues[priority]
            while q:
                if max_count is not None and len(result) >= max_count:
                    return result
                result.append(q.popleft())
        return result

    def __len__(self) -> int:
        return sum(len(q) for q in self._queues.values())

    def __bool__(self) -> bool:
        return any(self._queues.values())

    def _resolve_priority(self, event: EventParams) -> str:
        """Determine priority from the event's ``priority`` field."""
        priority = event.priority
        if priority is None or priority not in self._PRIORITY_ORDER:
            return "normal"
        return priority

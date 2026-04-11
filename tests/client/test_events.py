"""Tests for client-side event utilities: ProvenanceEnvelope and EventQueue."""

from __future__ import annotations

from datetime import datetime

from mcp.client.events import EventQueue, ProvenanceEnvelope
from mcp.types import EventParams

# ---------------------------------------------------------------------------
# ProvenanceEnvelope
# ---------------------------------------------------------------------------


class TestProvenanceEnvelope:
    def test_to_dict_all_fields(self) -> None:
        env = ProvenanceEnvelope(
            server="ci-server",
            server_trust="configured",
            topic="builds/myapp/status",
            source="ci/jenkins",
            event_id="evt_a1b2c3d4",
            received_at="2026-04-09T14:30:00Z",
            priority="high",
        )
        d = env.to_dict()
        # v2 normative attribute order: server, topic, priority, event_id, trust, source
        assert list(d.keys()) == ["server", "topic", "priority", "event_id", "trust", "source"]
        assert d == {
            "server": "ci-server",
            "topic": "builds/myapp/status",
            "priority": "high",
            "event_id": "evt_a1b2c3d4",
            "trust": "configured",
            "source": "ci/jenkins",
        }

    def test_to_dict_optional_none(self) -> None:
        env = ProvenanceEnvelope(
            server="my-server",
            server_trust="unknown",
            topic="test/topic",
        )
        d = env.to_dict()
        # trust is always emitted; event_id and source are omitted when None.
        assert list(d.keys()) == ["server", "topic", "priority", "trust"]
        assert d == {
            "server": "my-server",
            "topic": "test/topic",
            "priority": "normal",
            "trust": "unknown",
        }
        assert "source" not in d
        assert "event_id" not in d

    def test_to_xml_basic(self) -> None:
        env = ProvenanceEnvelope(
            server="spellbook",
            server_trust="trusted",
            topic="agents/worker-42/messages",
            source="spellbook/messaging",
            event_id="01KNY2QMDD",
            priority="high",
        )
        xml = env.to_xml('{"text": "hello"}')
        assert xml == (
            '<mcp:event server="spellbook" topic="agents/worker-42/messages"'
            ' priority="high" event_id="01KNY2QMDD" trust="trusted"'
            ' source="spellbook/messaging">\n'
            '{"text": "hello"}\n</mcp:event>'
        )

    def test_to_xml_empty_payload(self) -> None:
        env = ProvenanceEnvelope(server="s", server_trust="t", topic="x")
        xml = env.to_xml()
        assert xml.endswith(">\n\n</mcp:event>")

    def test_to_xml_with_special_chars_in_payload(self) -> None:
        env = ProvenanceEnvelope(server="s", server_trust="t", topic="x")
        xml = env.to_xml('<script>alert("xss")</script>')
        # Payload body must be escaped
        assert "<script>" not in xml
        assert "&lt;script&gt;" in xml

    def test_to_xml_with_special_chars_in_attrs(self) -> None:
        env = ProvenanceEnvelope(
            server='evil"server',
            server_trust="t",
            topic="x<y",
        )
        xml = env.to_xml("payload")
        # quoteattr switches to single-quote wrapping when value contains "
        assert "server='evil\"server'" in xml
        # quoteattr escapes < inside attribute values
        assert 'topic="x&lt;y"' in xml
        # trust attribute is always emitted
        assert 'trust="t"' in xml

    def test_to_xml_priority_defaults_normal(self) -> None:
        env = ProvenanceEnvelope(server="s", server_trust="t", topic="x")
        xml = env.to_xml("p")
        assert 'priority="normal"' in xml

    def test_from_event_extracts_fields(self) -> None:
        event = EventParams(
            topic="builds/status",
            eventId="evt_123",
            payload={"status": "ok"},
            source="ci/jenkins",
            priority="urgent",
        )
        env = ProvenanceEnvelope.from_event(event, server="ci-server", server_trust="configured")
        assert env.server == "ci-server"
        assert env.server_trust == "configured"
        assert env.topic == "builds/status"
        assert env.source == "ci/jenkins"
        assert env.event_id == "evt_123"
        assert env.priority == "urgent"
        assert env.received_at is not None
        # received_at must be a valid ISO 8601 timestamp
        datetime.fromisoformat(env.received_at)

    def test_from_event_no_source(self) -> None:
        event = EventParams(
            topic="test/topic",
            eventId="evt_456",
            payload={},
        )
        env = ProvenanceEnvelope.from_event(event, server="srv", server_trust="unknown")
        assert env.source is None
        assert env.priority == "normal"


# ---------------------------------------------------------------------------
# EventQueue
# ---------------------------------------------------------------------------


def _make_event(
    topic: str = "t",
    priority: str | None = None,
) -> EventParams:
    """Helper to create a minimal EventParams for queue tests."""
    return EventParams(
        topic=topic,
        eventId="e1",
        payload={},
        priority=priority,  # type: ignore[arg-type]
    )


class TestEventQueue:
    def test_enqueue_drain_priority_order(self) -> None:
        q = EventQueue()
        low = _make_event(topic="low", priority="low")
        normal = _make_event(topic="normal", priority="normal")
        high = _make_event(topic="high", priority="high")
        urgent = _make_event(topic="urgent", priority="urgent")

        # Enqueue in reverse priority order
        q.enqueue(low)
        q.enqueue(normal)
        q.enqueue(high)
        q.enqueue(urgent)

        result = q.drain()
        assert len(result) == 4
        # Should come out in priority order: urgent, high, normal, low
        assert [e.topic for e in result] == ["urgent", "high", "normal", "low"]

    def test_drain_max_count(self) -> None:
        q = EventQueue()
        for _ in range(10):
            q.enqueue(_make_event())
        result = q.drain(max_count=3)
        assert len(result) == 3
        assert len(q) == 7

    def test_drain_max_count_none(self) -> None:
        q = EventQueue()
        for _ in range(5):
            q.enqueue(_make_event())
        result = q.drain(max_count=None)
        assert len(result) == 5
        assert len(q) == 0

    def test_drain_empty_queue(self) -> None:
        q = EventQueue()
        result = q.drain()
        assert result == []

    def test_drain_empty_priority_levels(self) -> None:
        q = EventQueue()
        # Only enqueue at "urgent", leave other levels empty
        urgent = _make_event(topic="only-urgent", priority="urgent")
        q.enqueue(urgent)
        result = q.drain()
        assert len(result) == 1
        assert result[0].topic == "only-urgent"

    def test_len_and_bool(self) -> None:
        q = EventQueue()
        assert len(q) == 0
        assert not q

        q.enqueue(_make_event())
        assert len(q) == 1
        assert q

    def test_priority_no_field_defaults_normal(self) -> None:
        q = EventQueue()
        # Enqueue the low-priority event first
        low_event = _make_event(topic="low-event", priority="low")
        q.enqueue(low_event)
        event = _make_event(topic="no-priority", priority=None)
        q.enqueue(event)
        # no priority defaults to "normal", which ranks above "low"
        result = q.drain()
        assert len(result) == 2
        assert result[0].topic == "no-priority"
        assert result[1].topic == "low-event"

    def test_enqueue_drain_is_destructive(self) -> None:
        q = EventQueue()
        q.enqueue(_make_event())
        q.enqueue(_make_event())
        assert len(q) == 2
        q.drain()
        assert len(q) == 0
        assert not q

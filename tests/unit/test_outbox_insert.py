"""Unit tests for transactional outbox enqueue (Stage 2 Task 2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.ids import generate_uuidv7
from app.domain.models.outbox import OutboxEvent
from app.domain.services.outbox import enqueue_outbox
from app.integrations.kafka_producer import (
    OUTBOUND_PENDING_TOPIC,
    build_inbound_envelope,
    build_outbound_pending_envelope,
    inbound_topic,
)


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)


def test_enqueue_outbound_pending_sets_topic_payload_uuids_and_message_key() -> None:
    session = FakeSession()
    partner_public_id = generate_uuidv7()
    delivery_public_id = generate_uuidv7()
    endpoint_id = generate_uuidv7()
    correlation_id = str(generate_uuidv7())
    scheduled_at = datetime.now(UTC)
    sla_deadline_at = scheduled_at + timedelta(seconds=90)
    envelope = build_outbound_pending_envelope(
        delivery_public_id=delivery_public_id,
        partner_public_id=partner_public_id,
        endpoint_id=endpoint_id,
        event_type="order.created",
        payload={"order_id": "ord_1"},
        idempotency_key="idem-out-1",
        correlation_id=correlation_id,
        scheduled_at=scheduled_at,
        sla_deadline_at=sla_deadline_at,
        attempt=1,
    )

    event = enqueue_outbox(
        session,
        aggregate_type="delivery",
        aggregate_id=99,
        topic=OUTBOUND_PENDING_TOPIC,
        payload=envelope,
        key=str(partner_public_id),
    )

    assert isinstance(event, OutboxEvent)
    assert event.aggregate_type == "delivery"
    assert event.aggregate_id == 99
    assert event.topic == OUTBOUND_PENDING_TOPIC
    assert event.message_key == str(partner_public_id)
    assert event.payload == envelope
    assert event.published_at is None
    assert uuid.UUID(envelope["delivery_id"]).version == 7
    assert uuid.UUID(envelope["partner_id"]).version == 7
    assert uuid.UUID(envelope["endpoint_id"]).version == 7
    assert envelope["schema_version"] == 1
    assert session.added == [event]


def test_enqueue_inbound_sets_topic_payload_uuids_and_message_key() -> None:
    session = FakeSession()
    partner_public_id = generate_uuidv7()
    event_id = generate_uuidv7()
    correlation_id = str(generate_uuidv7())
    received_at = datetime.now(UTC)
    event_type = "order.created"
    envelope = build_inbound_envelope(
        event_id=event_id,
        partner_public_id=partner_public_id,
        event_type=event_type,
        payload={"order_id": "ord_in_1"},
        idempotency_key="idem-in-1",
        correlation_id=correlation_id,
        received_at=received_at,
    )

    event = enqueue_outbox(
        session,
        aggregate_type="inbound_event",
        aggregate_id=1,
        topic=inbound_topic(event_type),
        payload=envelope,
        key=str(partner_public_id),
    )

    assert event.topic == inbound_topic(event_type)
    assert event.message_key == str(partner_public_id)
    assert event.published_at is None
    assert uuid.UUID(envelope["event_id"]).version == 7
    assert uuid.UUID(envelope["partner_id"]).version == 7
    assert envelope["schema_version"] == 1
    assert session.added == [event]

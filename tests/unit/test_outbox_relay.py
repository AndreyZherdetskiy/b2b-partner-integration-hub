"""Unit tests for hub-outbox-relay publish_unpublished_batch."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from app.domain.ids import generate_uuidv7
from app.domain.models.outbox import OutboxEvent
from app.integrations.kafka_producer import (
    OUTBOUND_PENDING_TOPIC,
    build_inbound_envelope,
    build_outbound_pending_envelope,
    inbound_topic,
)
from app.observability.metrics import set_gauge_metric
from app.workers.outbox_relay import publish_unpublished_batch, unpublished_outbox_select


class _ScalarsResult:
    def __init__(self, items: list[OutboxEvent]) -> None:
        self._items = items

    def all(self) -> list[OutboxEvent]:
        return self._items


class _ExecuteResult:
    def __init__(
        self,
        *,
        events: list[OutboxEvent] | None = None,
        scalar: int | None = None,
    ) -> None:
        self._events = events
        self._scalar = scalar

    def scalars(self) -> _ScalarsResult:
        return _ScalarsResult(self._events or [])

    def scalar_one(self) -> int:
        assert self._scalar is not None
        return self._scalar


class FakeSession:
    def __init__(
        self,
        *,
        events: list[OutboxEvent] | None = None,
        unpublished_count: int = 0,
    ) -> None:
        self.events = list(events or [])
        self.unpublished_count = unpublished_count
        self.executed: list[object] = []

    async def execute(self, stmt: object) -> _ExecuteResult:
        self.executed.append(stmt)
        stmt_text = str(stmt)
        if "inbound_events" in stmt_text:
            return _ExecuteResult()
        if "count(" in stmt_text.lower():
            return _ExecuteResult(scalar=self.unpublished_count)
        return _ExecuteResult(events=self.events)


class FakeProducer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[
            tuple[str, str | None, dict[str, Any], list[tuple[str, bytes]] | None]
        ] = []

    async def send_and_wait(
        self,
        topic: str,
        *,
        key: str | None = None,
        value: dict[str, Any] | None = None,
        headers: list[tuple[str, bytes]] | None = None,
    ) -> object:
        if self.fail:
            raise RuntimeError("kafka publish failed")
        self.sent.append((topic, key, value or {}, headers))
        return None


def _outbound_event(*, partner_public_id: UUID, aggregate_id: int = 42) -> OutboxEvent:
    correlation_id = str(generate_uuidv7())
    scheduled_at = datetime.now(UTC)
    envelope = build_outbound_pending_envelope(
        delivery_public_id=generate_uuidv7(),
        partner_public_id=partner_public_id,
        endpoint_id=generate_uuidv7(),
        event_type="order.created",
        payload={"order_id": "ord_1"},
        idempotency_key="idem-1",
        correlation_id=correlation_id,
        scheduled_at=scheduled_at,
        sla_deadline_at=scheduled_at,
        attempt=1,
    )
    return OutboxEvent(
        aggregate_type="delivery",
        aggregate_id=aggregate_id,
        topic=OUTBOUND_PENDING_TOPIC,
        payload=envelope,
        message_key=str(partner_public_id),
        publish_attempts=0,
    )


def _inbound_event(
    *,
    partner_public_id: UUID,
    event_id: UUID,
    aggregate_id: int = 1,
) -> OutboxEvent:
    correlation_id = str(generate_uuidv7())
    event_type = "order.created"
    envelope = build_inbound_envelope(
        event_id=event_id,
        partner_public_id=partner_public_id,
        event_type=event_type,
        payload={"order_id": "ord_in"},
        idempotency_key="idem-in",
        correlation_id=correlation_id,
        received_at=datetime.now(UTC),
    )
    return OutboxEvent(
        aggregate_type="inbound_event",
        aggregate_id=aggregate_id,
        topic=inbound_topic(event_type),
        payload=envelope,
        message_key=str(partner_public_id),
        publish_attempts=0,
    )


@pytest.mark.asyncio
async def test_publish_success_sets_published_at_and_returns_one() -> None:
    partner_public_id = generate_uuidv7()
    event = _outbound_event(partner_public_id=partner_public_id)
    session = FakeSession(events=[event], unpublished_count=0)
    producer = FakeProducer()

    with patch("app.workers.outbox_relay.set_gauge_metric") as set_gauge:
        count = await publish_unpublished_batch(session, producer)

    assert count == 1
    assert event.published_at is not None
    assert len(producer.sent) == 1
    topic, key, value, headers = producer.sent[0]
    assert topic == OUTBOUND_PENDING_TOPIC
    assert key == str(partner_public_id)
    assert value == event.payload
    assert headers is not None
    assert ("correlation_id", event.payload["correlation_id"].encode("utf-8")) in headers
    assert ("delivery_id", event.payload["delivery_id"].encode("utf-8")) in headers
    set_gauge.assert_called_once_with("hub_outbox_unpublished", 0)


@pytest.mark.asyncio
async def test_publish_failure_increments_attempts_without_raising() -> None:
    event = _outbound_event(partner_public_id=generate_uuidv7())
    session = FakeSession(events=[event], unpublished_count=1)
    producer = FakeProducer(fail=True)

    with patch("app.workers.outbox_relay.set_gauge_metric"):
        count = await publish_unpublished_batch(session, producer)

    assert count == 0
    assert event.published_at is None
    assert event.publish_attempts == 1


def test_unpublished_select_uses_skip_locked_and_orders_by_created_at() -> None:
    stmt = unpublished_outbox_select(limit=25)
    compiled = str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        ),
    )
    assert "FOR UPDATE" in compiled.upper()
    assert "SKIP LOCKED" in compiled.upper()
    assert "ORDER BY" in compiled.upper()
    assert "created_at" in compiled


@pytest.mark.asyncio
async def test_inbound_success_triggers_inbound_published_at_update() -> None:
    partner_public_id = generate_uuidv7()
    event_id = generate_uuidv7()
    event = _inbound_event(
        partner_public_id=partner_public_id,
        event_id=event_id,
    )
    session = FakeSession(events=[event], unpublished_count=0)
    producer = FakeProducer()

    with patch("app.workers.outbox_relay.set_gauge_metric"):
        count = await publish_unpublished_batch(session, producer)

    assert count == 1
    assert event.published_at is not None
    inbound_updates = [stmt for stmt in session.executed if "inbound_events" in str(stmt)]
    assert len(inbound_updates) == 1
    compiled = str(
        inbound_updates[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        ),
    )
    assert str(event_id) in compiled


def test_set_gauge_metric_rejects_forbidden_attributes() -> None:
    with pytest.raises(ValueError, match="partner_id"):
        set_gauge_metric("hub_outbox_unpublished", 3, attributes={"partner_id": "uuid"})


def test_set_gauge_metric_allows_unlabeled_gauge() -> None:
    set_gauge_metric("hub_outbox_unpublished", 0)


@pytest.mark.asyncio
async def test_empty_unpublished_batch_returns_zero_without_producer_call() -> None:
    session = FakeSession(events=[], unpublished_count=0)
    producer = FakeProducer()

    with patch("app.workers.outbox_relay.set_gauge_metric") as set_gauge:
        count = await publish_unpublished_batch(session, producer)

    assert count == 0
    assert producer.sent == []
    set_gauge.assert_called_once_with("hub_outbox_unpublished", 0)

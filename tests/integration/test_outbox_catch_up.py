"""Integration tests for outbox relay catch-up (Stage 2 Task 3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from tests.fixtures.kafka_helpers import RecordingKafkaProducer

from app.domain.ids import generate_uuidv7
from app.domain.models.outbox import OutboxEvent
from app.domain.models.partner import Partner
from app.domain.services.outbox import enqueue_outbox
from app.integrations.kafka_producer import (
    OUTBOUND_PENDING_TOPIC,
    build_outbound_pending_envelope,
)
from app.workers.outbox_relay import publish_unpublished_batch

pytestmark = pytest.mark.integration


class FailingKafkaProducer:
    async def send_and_wait(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("kafka unavailable")


@pytest.mark.asyncio
async def test_outbox_catch_up_failure_then_success(db_engine: AsyncEngine) -> None:
    partner_public_id = generate_uuidv7()
    delivery_public_id = generate_uuidv7()
    endpoint_id = generate_uuidv7()
    correlation_id = str(generate_uuidv7())
    scheduled_at = datetime.now(UTC)
    envelope = build_outbound_pending_envelope(
        delivery_public_id=delivery_public_id,
        partner_public_id=partner_public_id,
        endpoint_id=endpoint_id,
        event_type="order.created",
        payload={"order_id": "catch-up-1"},
        idempotency_key="idem-catch-up",
        correlation_id=correlation_id,
        scheduled_at=scheduled_at,
        sla_deadline_at=scheduled_at + timedelta(seconds=90),
        attempt=1,
    )

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        partner = Partner(
            slug="catch-up-partner",
            name="Catch Up Partner",
            status="active",
            sla_seconds=120,
            rate_limit_rps=100,
            signing_secret_encrypted=b"enc",
        )
        session.add(partner)
        await session.flush()
        outbox_event = enqueue_outbox(
            session,
            aggregate_type="delivery",
            aggregate_id=99,
            topic=OUTBOUND_PENDING_TOPIC,
            payload=envelope,
            key=str(partner_public_id),
        )
        await session.commit()
        outbox_id = outbox_event.id

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        count = await publish_unpublished_batch(session, FailingKafkaProducer())
        await session.commit()

    assert count == 0

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        row = (
            await session.execute(select(OutboxEvent).where(OutboxEvent.id == outbox_id))
        ).scalar_one()
        assert row.published_at is None
        assert row.publish_attempts >= 1

    producer = RecordingKafkaProducer()
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        count = await publish_unpublished_batch(session, producer)
        await session.commit()

    assert count == 1
    assert producer.messages
    topic, key, value = producer.messages[0]
    assert topic == OUTBOUND_PENDING_TOPIC
    assert key == str(partner_public_id)
    assert value["delivery_id"] == str(delivery_public_id)

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        row = (
            await session.execute(select(OutboxEvent).where(OutboxEvent.id == outbox_id))
        ).scalar_one()
        assert row.published_at is not None

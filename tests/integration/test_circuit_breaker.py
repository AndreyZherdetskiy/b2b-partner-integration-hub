"""Integration tests for per-partner circuit breaker."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.kafka_helpers import RecordingKafkaProducer
from tests.fixtures.partner_factory import (
    create_outbound_partner,
    create_pending_delivery,
    outbound_envelope,
)
from tests.unit.test_circuit_breaker import FakeRedis

from app.config import Settings
from app.domain.enums import DeliveryStatus
from app.domain.models.delivery import Delivery
from app.domain.services.circuit_breaker import CircuitState
from app.integrations.kafka_producer import OUTBOUND_DLQ_TOPIC, OUTBOUND_RETRY_30S_TOPIC
from app.workers.outbound_processor import ProcessOutcome, process_outbound_message

pytestmark = pytest.mark.integration

FIXED_NOW = datetime(2026, 3, 16, 12, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_open_circuit_pauses_delivery_without_post_or_dlq(
    db_engine,
    fernet_key: str,
) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        partner, endpoint, _scenario = await create_outbound_partner(
            session,
            fernet_key=fernet_key,
            slug="circuit-pause",
        )
        delivery = await create_pending_delivery(session, partner=partner, endpoint=endpoint)

    producer = RecordingKafkaProducer()
    envelope = outbound_envelope(delivery, partner, endpoint, now=FIXED_NOW)
    redis = FakeRedis()
    redis.store[f"cb:{partner.slug}:state"] = CircuitState.OPEN.value.encode()
    redis.store[f"cb:{partner.slug}:opened_at"] = FIXED_NOW.isoformat().encode()
    settings = Settings(
        fernet_key=fernet_key,
        hub_backoff_base_seconds=30,
        hub_backoff_multiplier=2,
        hub_backoff_max_seconds=3600,
    )
    post_mock = AsyncMock()

    with (
        patch("app.domain.services.circuit_breaker._utcnow", return_value=FIXED_NOW),
        patch("app.workers.outbound_processor.post_outbound", new=post_mock),
    ):
        async with AsyncSession(db_engine, expire_on_commit=False) as session:
            outcome = await process_outbound_message(
                session,
                producer,
                envelope,
                settings,
                now=FIXED_NOW,
                redis=redis,
            )

    assert outcome == ProcessOutcome.RETRY_SCHEDULED
    post_mock.assert_not_called()
    assert OUTBOUND_RETRY_30S_TOPIC in producer.topics()
    assert OUTBOUND_DLQ_TOPIC not in producer.topics()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        row = await session.execute(
            select(Delivery).where(Delivery.public_id == delivery.public_id)
        )
        refreshed = row.scalar_one()
        assert refreshed.status == DeliveryStatus.RETRYING.value
        assert refreshed.attempt_count == 0

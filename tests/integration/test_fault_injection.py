"""Fault-injection integration subset (spec §10.3)."""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from tests.fixtures.kafka_helpers import RecordingKafkaProducer
from tests.fixtures.partner_factory import (
    create_outbound_partner,
    create_pending_delivery,
    outbound_envelope,
)
from tests.integration.test_inbound_idempotency import test_idempotency_duplicate_suppresses_kafka
from tests.unit.test_circuit_breaker import FakeRedis

from app.config import Settings, get_settings
from app.domain.enums import DeliveryStatus
from app.domain.models.attempt import DeliveryAttempt
from app.domain.models.audit import AuditLog
from app.domain.models.dead_letter import DeadLetter
from app.domain.models.delivery import Delivery
from app.domain.services.replay_service import bulk_replay_deliveries, replay_delivery
from app.integrations.http_client import post_outbound
from app.integrations.kafka_producer import OUTBOUND_DLQ_TOPIC, OUTBOUND_RETRY_30S_TOPIC
from app.workers.outbound_processor import ProcessOutcome, process_outbound_message

pytestmark = pytest.mark.integration

PARTNER_MOCK_HEALTH = os.getenv("PARTNER_MOCK_HEALTH", "http://localhost:8090/health")
_sigsegv_abort = False


def _url_reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


@pytest.fixture
def _require_infra() -> None:
    global _sigsegv_abort
    if _sigsegv_abort:
        pytest.skip("prior e2e aborted after httpx+asyncpg SIGSEGV")
    if not _url_reachable(PARTNER_MOCK_HEALTH):
        pytest.skip("partner-mock not reachable — start compose partner-mock for integration")


def _settings():
    return get_settings()


_fail_503_then_ok_counts: dict[str, int] = {}


@asynccontextmanager
async def _mock_scenario_transport(scenario: str) -> AsyncIterator[httpx.AsyncClient]:
    async def _post_with_scenario(**kwargs: object) -> object:
        headers = dict(kwargs["headers"])  # type: ignore[arg-type]
        effective = scenario
        if scenario == "fail_503_then_ok":
            idem = headers.get("Idempotency-Key", "")
            attempt = _fail_503_then_ok_counts.get(idem, 0) + 1
            _fail_503_then_ok_counts[idem] = attempt
            effective = "fail_503" if attempt <= 3 else "ok"
        headers["X-Mock-Scenario"] = effective
        return await post_outbound(**{**kwargs, "headers": headers})  # type: ignore[arg-type]

    with patch("app.workers.outbound_processor.post_outbound", side_effect=_post_with_scenario):
        async with httpx.AsyncClient() as http_client:
            yield http_client


async def _process_until(
    engine: AsyncEngine,
    *,
    envelope: dict,
    producer: RecordingKafkaProducer,
    http_client: httpx.AsyncClient,
    max_rounds: int = 6,
) -> ProcessOutcome:
    outcome = ProcessOutcome.SKIPPED
    for _ in range(max_rounds):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            outcome = await process_outbound_message(
                session,
                producer,
                envelope,
                _settings(),
                http_client=http_client,
            )
        if outcome in {ProcessOutcome.DELIVERED, ProcessOutcome.DLQ, ProcessOutcome.SKIPPED}:
            break
    return outcome


@pytest.mark.asyncio
async def test_partner_503_then_200_eventually_delivered(
    _require_infra: None,
    db_engine: AsyncEngine,
    fernet_key: str,
) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        partner, endpoint, scenario = await create_outbound_partner(
            session,
            fernet_key=fernet_key,
            slug="flaky-logistics",
            mock_scenario="fail_503_then_ok",
        )
        delivery = await create_pending_delivery(session, partner=partner, endpoint=endpoint)

    producer = RecordingKafkaProducer()
    envelope = outbound_envelope(delivery, partner, endpoint)
    async with _mock_scenario_transport(scenario) as http_client:
        outcome = await _process_until(
            db_engine,
            envelope=envelope,
            producer=producer,
            http_client=http_client,
        )

    assert outcome == ProcessOutcome.DELIVERED
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        row = await session.execute(
            select(Delivery).where(Delivery.public_id == delivery.public_id)
        )
        refreshed = row.scalar_one()
        assert refreshed.status == DeliveryStatus.DELIVERED.value
        assert refreshed.first_success_at is not None

        attempts = (
            (
                await session.execute(
                    select(DeliveryAttempt)
                    .where(DeliveryAttempt.delivery_id == refreshed.id)
                    .order_by(DeliveryAttempt.attempt_number)
                )
            )
            .scalars()
            .all()
        )
        assert len(attempts) == 4
        assert [a.http_status_code for a in attempts[:3]] == [503, 503, 503]
        assert attempts[3].http_status_code == 200


@pytest.mark.asyncio
async def test_partner_400_failed_dlq_no_retry(
    _require_infra: None,
    db_engine: AsyncEngine,
    fernet_key: str,
) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        partner, endpoint, scenario = await create_outbound_partner(
            session,
            fernet_key=fernet_key,
            slug="strict-payments",
            mock_scenario="fail_400",
        )
        delivery = await create_pending_delivery(session, partner=partner, endpoint=endpoint)

    producer = RecordingKafkaProducer()
    envelope = outbound_envelope(delivery, partner, endpoint)
    async with (
        _mock_scenario_transport(scenario) as http_client,
        AsyncSession(db_engine, expire_on_commit=False) as session,
    ):
        outcome = await process_outbound_message(
            session,
            producer,
            envelope,
            _settings(),
            http_client=http_client,
        )

    assert outcome == ProcessOutcome.DLQ
    assert OUTBOUND_RETRY_30S_TOPIC not in producer.topics()
    assert OUTBOUND_DLQ_TOPIC in producer.topics()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        row = await session.execute(
            select(Delivery).where(Delivery.public_id == delivery.public_id)
        )
        refreshed = row.scalar_one()
        assert refreshed.status == DeliveryStatus.FAILED.value

        dlq = (
            await session.execute(select(DeadLetter).where(DeadLetter.delivery_id == refreshed.id))
        ).scalar_one_or_none()
        assert dlq is not None


@pytest.mark.asyncio
async def test_timeout_schedules_retry_and_logs_attempt(
    _require_infra: None,
    db_engine: AsyncEngine,
    fernet_key: str,
) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        partner, endpoint, scenario = await create_outbound_partner(
            session,
            fernet_key=fernet_key,
            slug="slow-crm",
            mock_scenario="timeout",
            timeout_read_ms=500,
            timeout_connect_ms=500,
        )
        delivery = await create_pending_delivery(session, partner=partner, endpoint=endpoint)

    producer = RecordingKafkaProducer()
    envelope = outbound_envelope(delivery, partner, endpoint)
    async with (
        _mock_scenario_transport(scenario) as http_client,
        AsyncSession(db_engine, expire_on_commit=False) as session,
    ):
        outcome = await process_outbound_message(
            session,
            producer,
            envelope,
            _settings(),
            http_client=http_client,
        )

    assert outcome == ProcessOutcome.RETRY_SCHEDULED
    assert OUTBOUND_RETRY_30S_TOPIC in producer.topics()
    assert OUTBOUND_DLQ_TOPIC not in producer.topics()

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        row = await session.execute(
            select(Delivery).where(Delivery.public_id == delivery.public_id)
        )
        refreshed = row.scalar_one()
        assert refreshed.status == DeliveryStatus.RETRYING.value

        attempt = (
            await session.execute(
                select(DeliveryAttempt).where(DeliveryAttempt.delivery_id == refreshed.id)
            )
        ).scalar_one()
        assert attempt.error_type == "timeout"


@pytest.mark.asyncio
async def test_duplicate_inbound_idempotency_one_kafka_message(
    _require_infra: None,
    client: TestClient,
) -> None:
    await test_idempotency_duplicate_suppresses_kafka(client)


@pytest.mark.asyncio
async def test_manual_replay_after_repair_delivered_with_audit(
    _require_infra: None,
    db_engine: AsyncEngine,
    fernet_key: str,
) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        partner, endpoint, scenario = await create_outbound_partner(
            session,
            fernet_key=fernet_key,
            slug="replay-repair",
            mock_scenario="ok",
        )
        delivery = await create_pending_delivery(
            session,
            partner=partner,
            endpoint=endpoint,
            status=DeliveryStatus.FAILED,
            attempt_count=2,
        )

    producer = RecordingKafkaProducer()
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        replayed = await replay_delivery(
            session,
            delivery_public_id=delivery.public_id,
            actor_id="operator-replay",
            reason="partner endpoint restored",
            reset_attempt_counter=False,
        )
    assert replayed.status == DeliveryStatus.REPLAYING.value

    envelope = outbound_envelope(replayed, partner, endpoint, now=datetime.now(UTC))
    async with (
        _mock_scenario_transport(scenario) as http_client,
        AsyncSession(db_engine, expire_on_commit=False) as session,
    ):
        outcome = await process_outbound_message(
            session,
            producer,
            envelope,
            _settings(),
            http_client=http_client,
        )

    assert outcome == ProcessOutcome.DELIVERED

    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        row = await session.execute(
            select(Delivery).where(Delivery.public_id == delivery.public_id)
        )
        refreshed = row.scalar_one()
        assert refreshed.status == DeliveryStatus.DELIVERED.value
        assert refreshed.first_success_at is not None

        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "delivery.replay",
                    AuditLog.resource_id == delivery.public_id,
                )
            )
        ).scalar_one_or_none()
        assert audit is not None
        assert audit.actor_id == "operator-replay"


@pytest.mark.stage2
@pytest.mark.asyncio
async def test_burst_replay_rate_limit_stage2(
    db_engine: AsyncEngine,
    fernet_key: str,
) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        partner, endpoint, _scenario = await create_outbound_partner(
            session,
            fernet_key=fernet_key,
            slug="burst-replay-rl",
        )
        partner.rate_limit_rps = 1
        await session.commit()
        delivery_first = await create_pending_delivery(
            session,
            partner=partner,
            endpoint=endpoint,
            status=DeliveryStatus.FAILED,
        )
        delivery_second = await create_pending_delivery(
            session,
            partner=partner,
            endpoint=endpoint,
            status=DeliveryStatus.FAILED,
        )

    redis = FakeRedis()
    settings = Settings(fernet_key=fernet_key)
    fixed_now = 1_700_000_000.0

    with patch("app.domain.services.rate_limit.time.time", return_value=fixed_now):
        async with AsyncSession(db_engine, expire_on_commit=False) as session:
            result = await bulk_replay_deliveries(
                session,
                delivery_public_ids=[delivery_first.public_id, delivery_second.public_id],
                actor_id="operator-bulk",
                reason="burst recovery",
                redis=redis,  # type: ignore[arg-type]
                settings=settings,
            )

    assert result.replayed == [delivery_first.public_id]
    assert result.skipped_rate_limited == [delivery_second.public_id]

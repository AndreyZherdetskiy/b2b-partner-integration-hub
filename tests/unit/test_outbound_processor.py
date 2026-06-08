"""Unit tests for outbound delivery processing (Task 12)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.sql.selectable import Select

from app.config import Settings
from app.domain.enums import (
    DeliveryDirection,
    DeliveryStatus,
    EndpointDirection,
    EndpointStatus,
    PartnerStatus,
)
from app.domain.ids import generate_uuidv7
from app.domain.models.attempt import DeliveryAttempt
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.domain.services.circuit_breaker import CircuitState
from app.domain.services.hmac_service import verify
from app.domain.services.retry_topics import (
    OUTBOUND_RETRY_1M_TOPIC,
    OUTBOUND_RETRY_30S_TOPIC,
)
from app.domain.services.secrets import encrypt_signing_secret
from app.integrations.http_client import OutboundPostResult, serialize_payload
from app.integrations.kafka_producer import OUTBOUND_DLQ_TOPIC
from app.workers.outbound_processor import ProcessOutcome, process_outbound_message

FIXED_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
FERNET_KEY = Fernet.generate_key().decode("ascii")
SIGNING_SECRET = b"partner-signing-secret"
PARTNER_SLUG = "outbound-test"
EVENT_TYPE = "order.created"


class FakeResult:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def one_or_none(self) -> object | None:
        return self._row

    def scalars(self) -> FakeResult:
        return self

    def all(self) -> list[object]:
        if self._row is None:
            return []
        if isinstance(self._row, list):
            return self._row
        return [self._row]

    def scalar_one_or_none(self) -> object | None:
        if self._row is None:
            return None
        if isinstance(self._row, list):
            return self._row[0] if self._row else None
        return self._row


def _targets_signing_secrets(stmt: object) -> bool:
    if not isinstance(stmt, Select):
        return False
    for entity in stmt._raw_columns:
        table = getattr(getattr(entity, "entity_namespace", None), "__table__", None)
        if table is not None and table.name == "partner_signing_secrets":
            return True
    return False


class ProcessorSession:
    def __init__(self, row: tuple[Delivery, Partner, PartnerEndpoint] | None) -> None:
        self._row = row
        self.committed = False
        self.added: list[object] = []

    async def execute(self, stmt: object) -> FakeResult:
        if _targets_signing_secrets(stmt):
            return FakeResult([])
        return FakeResult(self._row)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


class FakeKafkaProducer:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str | None, dict[str, Any]]] = []

    async def send_and_wait(
        self,
        topic: str,
        *,
        key: str | None = None,
        value: dict[str, Any] | None = None,
        headers: list[tuple[str, bytes]] | None = None,
    ) -> object:
        self.messages.append((topic, key, value or {}))
        return None


def _settings() -> Settings:
    return Settings(
        fernet_key=FERNET_KEY,
        hub_backoff_base_seconds=30,
        hub_backoff_multiplier=2,
        hub_backoff_max_seconds=3600,
    )


def _partner() -> Partner:
    return Partner(
        id=1,
        public_id=generate_uuidv7(),
        slug=PARTNER_SLUG,
        name="Outbound Test",
        status=PartnerStatus.ACTIVE,
        sla_seconds=120,
        rate_limit_rps=100,
        signing_secret_encrypted=encrypt_signing_secret(SIGNING_SECRET, FERNET_KEY),
    )


def _endpoint(partner: Partner) -> PartnerEndpoint:
    return PartnerEndpoint(
        id=generate_uuidv7(),
        partner_id=partner.id,
        direction=EndpointDirection.OUTBOUND,
        url="http://partner-mock:8090/hooks",
        event_types=[EVENT_TYPE],
        status=EndpointStatus.ACTIVE,
        sla_seconds=90,
        max_attempts=3,
        retry_on_status_codes=[],
        timeout_connect_ms=1000,
        timeout_read_ms=2000,
    )


def _delivery(
    partner: Partner,
    endpoint: PartnerEndpoint,
    *,
    status: DeliveryStatus = DeliveryStatus.PENDING,
    attempt_count: int = 0,
) -> Delivery:
    return Delivery(
        id=42,
        public_id=generate_uuidv7(),
        partner_id=partner.id,
        endpoint_id=endpoint.id,
        direction=DeliveryDirection.OUTBOUND,
        event_type=EVENT_TYPE,
        idempotency_key="idem-1",
        payload={"order_id": "o-1"},
        payload_hash="hash",
        status=status,
        attempt_count=attempt_count,
        max_attempts=endpoint.max_attempts,
        sla_deadline_at=FIXED_NOW + timedelta(seconds=120),
        correlation_id=str(generate_uuidv7()),
    )


def _envelope(delivery: Delivery, partner: Partner, endpoint: PartnerEndpoint) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "delivery_id": str(delivery.public_id),
        "partner_id": str(partner.public_id),
        "endpoint_id": str(endpoint.id),
        "event_type": EVENT_TYPE,
        "attempt": delivery.attempt_count + 1,
        "payload": delivery.payload,
        "idempotency_key": delivery.idempotency_key,
        "correlation_id": delivery.correlation_id,
        "scheduled_at": FIXED_NOW.isoformat(),
        "sla_deadline_at": delivery.sla_deadline_at.isoformat(),
    }


@pytest.mark.asyncio
async def test_process_outbound_200_delivered() -> None:
    partner = _partner()
    endpoint = _endpoint(partner)
    delivery = _delivery(partner, endpoint)
    session = ProcessorSession((delivery, partner, endpoint))
    producer = FakeKafkaProducer()

    with patch(
        "app.workers.outbound_processor.post_outbound",
        new=AsyncMock(
            return_value=OutboundPostResult(
                http_status_code=200,
                response_headers={},
                response_body="ok",
                duration_ms=12,
                error_type=None,
            )
        ),
    ):
        outcome = await process_outbound_message(
            session,
            producer,
            _envelope(delivery, partner, endpoint),
            _settings(),
            now=FIXED_NOW,
        )

    assert outcome == ProcessOutcome.DELIVERED
    assert delivery.status == DeliveryStatus.DELIVERED.value
    assert delivery.first_success_at == FIXED_NOW
    assert session.committed
    assert not any(topic == OUTBOUND_RETRY_30S_TOPIC for topic, _, _ in producer.messages)
    assert not any(topic == OUTBOUND_DLQ_TOPIC for topic, _, _ in producer.messages)


@pytest.mark.asyncio
async def test_process_outbound_400_dlq_no_retry() -> None:
    partner = _partner()
    endpoint = _endpoint(partner)
    delivery = _delivery(partner, endpoint)
    session = ProcessorSession((delivery, partner, endpoint))
    producer = FakeKafkaProducer()

    with patch(
        "app.workers.outbound_processor.post_outbound",
        new=AsyncMock(
            return_value=OutboundPostResult(
                http_status_code=400,
                response_headers={},
                response_body="bad request",
                duration_ms=8,
                error_type=None,
            )
        ),
    ):
        outcome = await process_outbound_message(
            session,
            producer,
            _envelope(delivery, partner, endpoint),
            _settings(),
            now=FIXED_NOW,
        )

    assert outcome == ProcessOutcome.DLQ
    assert delivery.status == DeliveryStatus.FAILED.value
    assert any(topic == OUTBOUND_DLQ_TOPIC for topic, _, _ in producer.messages)
    assert not any(topic == OUTBOUND_RETRY_30S_TOPIC for topic, _, _ in producer.messages)


@pytest.mark.asyncio
async def test_process_outbound_503_schedules_retry() -> None:
    partner = _partner()
    endpoint = _endpoint(partner)
    delivery = _delivery(partner, endpoint)
    session = ProcessorSession((delivery, partner, endpoint))
    producer = FakeKafkaProducer()

    with patch(
        "app.workers.outbound_processor.post_outbound",
        new=AsyncMock(
            return_value=OutboundPostResult(
                http_status_code=503,
                response_headers={},
                response_body="unavailable",
                duration_ms=15,
                error_type=None,
            )
        ),
    ):
        outcome = await process_outbound_message(
            session,
            producer,
            _envelope(delivery, partner, endpoint),
            _settings(),
            now=FIXED_NOW,
        )

    assert outcome == ProcessOutcome.RETRY_SCHEDULED
    assert delivery.status == DeliveryStatus.RETRYING.value
    assert delivery.attempt_count == 1
    assert delivery.next_retry_at is not None
    assert any(topic == OUTBOUND_RETRY_30S_TOPIC for topic, _, _ in producer.messages)
    assert not any(topic == OUTBOUND_DLQ_TOPIC for topic, _, _ in producer.messages)


@pytest.mark.asyncio
async def test_process_outbound_timeout_retryable() -> None:
    partner = _partner()
    endpoint = _endpoint(partner)
    delivery = _delivery(partner, endpoint)
    session = ProcessorSession((delivery, partner, endpoint))
    producer = FakeKafkaProducer()

    with patch(
        "app.workers.outbound_processor.post_outbound",
        new=AsyncMock(
            return_value=OutboundPostResult(
                http_status_code=None,
                response_headers={},
                response_body="",
                duration_ms=2000,
                error_type="timeout",
            )
        ),
    ):
        outcome = await process_outbound_message(
            session,
            producer,
            _envelope(delivery, partner, endpoint),
            _settings(),
            now=FIXED_NOW,
        )

    assert outcome == ProcessOutcome.RETRY_SCHEDULED
    assert any(topic == OUTBOUND_RETRY_30S_TOPIC for topic, _, _ in producer.messages)


@pytest.mark.asyncio
async def test_process_outbound_skips_terminal_status() -> None:
    partner = _partner()
    endpoint = _endpoint(partner)
    delivery = _delivery(partner, endpoint, status=DeliveryStatus.DELIVERED)
    session = ProcessorSession((delivery, partner, endpoint))
    producer = FakeKafkaProducer()

    outcome = await process_outbound_message(
        session,
        producer,
        _envelope(delivery, partner, endpoint),
        _settings(),
        now=FIXED_NOW,
    )

    assert outcome == ProcessOutcome.SKIPPED
    assert not session.committed
    assert producer.messages == []


@pytest.mark.asyncio
async def test_outbound_hmac_verifies_on_raw_body() -> None:
    partner = _partner()
    endpoint = _endpoint(partner)
    delivery = _delivery(partner, endpoint)
    captured: dict[str, Any] = {}

    async def _capture_post(**kwargs: Any) -> OutboundPostResult:
        captured.update(kwargs)
        return OutboundPostResult(200, {}, "ok", 5, None)

    session = ProcessorSession((delivery, partner, endpoint))
    producer = FakeKafkaProducer()

    with patch("app.workers.outbound_processor.post_outbound", new=_capture_post):
        await process_outbound_message(
            session,
            producer,
            _envelope(delivery, partner, endpoint),
            _settings(),
            now=FIXED_NOW,
        )

    body_bytes = captured["body_bytes"]
    headers = captured["headers"]
    timestamp = headers["X-Hub-Timestamp"]
    signature = headers["X-Hub-Signature-256"]
    assert verify(SIGNING_SECRET, timestamp, body_bytes, signature, now=int(timestamp))
    assert body_bytes == serialize_payload(delivery.payload)


@pytest.mark.asyncio
async def test_process_outbound_second_failure_publishes_1m_topic() -> None:
    partner = _partner()
    endpoint = _endpoint(partner)
    delivery = _delivery(
        partner,
        endpoint,
        status=DeliveryStatus.RETRYING,
        attempt_count=1,
    )
    session = ProcessorSession((delivery, partner, endpoint))
    producer = FakeKafkaProducer()

    with patch(
        "app.workers.outbound_processor.post_outbound",
        new=AsyncMock(
            return_value=OutboundPostResult(
                http_status_code=503,
                response_headers={},
                response_body="unavailable",
                duration_ms=15,
                error_type=None,
            )
        ),
    ):
        outcome = await process_outbound_message(
            session,
            producer,
            _envelope(delivery, partner, endpoint),
            _settings(),
            now=FIXED_NOW,
        )

    assert outcome == ProcessOutcome.RETRY_SCHEDULED
    assert any(topic == OUTBOUND_RETRY_1M_TOPIC for topic, _, _ in producer.messages)
    assert not any(topic == OUTBOUND_RETRY_30S_TOPIC for topic, _, _ in producer.messages)


@pytest.mark.asyncio
async def test_retry_envelope_uses_public_uuids_only() -> None:
    partner = _partner()
    endpoint = _endpoint(partner)
    delivery = _delivery(partner, endpoint)
    session = ProcessorSession((delivery, partner, endpoint))
    producer = FakeKafkaProducer()

    with patch(
        "app.workers.outbound_processor.post_outbound",
        new=AsyncMock(
            return_value=OutboundPostResult(503, {}, "down", 10, None),
        ),
    ):
        await process_outbound_message(
            session,
            producer,
            _envelope(delivery, partner, endpoint),
            _settings(),
            now=FIXED_NOW,
        )

    retry_messages = [
        value for topic, _, value in producer.messages if topic == OUTBOUND_RETRY_30S_TOPIC
    ]
    assert len(retry_messages) == 1
    envelope = retry_messages[0]
    assert envelope["delivery_id"] == str(delivery.public_id)
    assert envelope["partner_id"] == str(partner.public_id)
    assert envelope["endpoint_id"] == str(endpoint.id)
    for value in envelope.values():
        if isinstance(value, str) and value.isdigit() and len(value) < 10:
            pytest.fail("BIGINT leaked into Kafka envelope")


class CircuitFakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(
        self,
        key: str,
        value: str | bytes,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        raw = value.encode() if isinstance(value, str) else value
        if nx and key in self.store:
            return False
        self.store[key] = raw
        return True

    async def incr(self, key: str) -> int:
        current = int(self.store.get(key, b"0").decode())
        current += 1
        self.store[key] = str(current).encode()
        return current

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                removed += 1
        return removed


@pytest.mark.asyncio
async def test_open_circuit_skips_post_schedules_retry_without_dlq() -> None:
    partner = _partner()
    endpoint = _endpoint(partner)
    delivery = _delivery(partner, endpoint)
    session = ProcessorSession((delivery, partner, endpoint))
    producer = FakeKafkaProducer()
    redis = CircuitFakeRedis()
    redis.store[f"cb:{PARTNER_SLUG}:state"] = CircuitState.OPEN.value.encode()
    redis.store[f"cb:{PARTNER_SLUG}:opened_at"] = FIXED_NOW.isoformat().encode()

    post_mock = AsyncMock(
        return_value=OutboundPostResult(
            http_status_code=200,
            response_headers={},
            response_body="ok",
            duration_ms=5,
            error_type=None,
        )
    )

    with (
        patch("app.domain.services.circuit_breaker._utcnow", return_value=FIXED_NOW),
        patch("app.workers.outbound_processor.post_outbound", new=post_mock),
    ):
        outcome = await process_outbound_message(
            session,
            producer,
            _envelope(delivery, partner, endpoint),
            _settings(),
            now=FIXED_NOW,
            redis=redis,
        )

    assert outcome == ProcessOutcome.RETRY_SCHEDULED
    post_mock.assert_not_called()
    assert delivery.attempt_count == 0
    assert delivery.status == DeliveryStatus.RETRYING.value
    assert any(topic == OUTBOUND_RETRY_30S_TOPIC for topic, _, _ in producer.messages)
    assert not any(topic == OUTBOUND_DLQ_TOPIC for topic, _, _ in producer.messages)
    assert not any(isinstance(obj, DeliveryAttempt) for obj in session.added)

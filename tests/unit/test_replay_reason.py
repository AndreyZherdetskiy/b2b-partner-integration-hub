"""Unit tests for admin delivery replay auth and validation."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.config import get_settings
from app.domain.enums import DeliveryStatus, EndpointDirection, EndpointStatus, PartnerStatus
from app.domain.ids import generate_uuidv7
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.outbox import OutboxEvent
from app.domain.models.partner import Partner
from app.main import create_app

ADMIN_TOKEN = "test-admin-bootstrap-token-at-least-32-bytes"
PARTNER_SLUG = "replay-acme"
EVENT_TYPE = "order.created"


class FakeResult:
    def __init__(
        self,
        *,
        scalar: object = None,
        rows: list[object] | None = None,
        row: object = None,
    ) -> None:
        self._scalar = scalar
        self._rows = rows if rows is not None else []
        self._row = row

    def scalar_one(self) -> object:
        return self._scalar

    def scalar_one_or_none(self) -> object:
        return self._scalar

    def one_or_none(self) -> object:
        return self._row

    def all(self) -> list[object]:
        return self._rows

    def scalars(self) -> FakeResult:
        return self


class ReplaySession:
    def __init__(self, delivery: Delivery | None, partner: Partner | None) -> None:
        self._delivery = delivery
        self._partner = partner
        self.committed = False
        self.added: list[object] = []

    async def execute(self, _stmt: object) -> FakeResult:
        if self._delivery is None:
            return FakeResult(row=None)
        return FakeResult(row=(self._delivery, self._partner))

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, _obj: object) -> None:
        return None


class FakeKafkaProducer:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str | None, dict[str, Any], list[tuple[str, bytes]]]] = []
        self.call_count = 0

    async def send_and_wait(
        self,
        topic: str,
        *,
        key: str | None = None,
        value: dict[str, Any] | None = None,
        headers: list[tuple[str, bytes]] | None = None,
    ) -> object:
        self.call_count += 1
        self.messages.append((topic, key, value or {}, headers or []))
        return MagicMock()


@pytest.fixture
def partner() -> Partner:
    return Partner(
        id=1,
        public_id=generate_uuidv7(),
        slug=PARTNER_SLUG,
        name="ACME",
        status=PartnerStatus.ACTIVE,
        sla_seconds=120,
        rate_limit_rps=100,
        signing_secret_encrypted=None,
    )


@pytest.fixture
def endpoint(partner: Partner) -> PartnerEndpoint:
    return PartnerEndpoint(
        id=generate_uuidv7(),
        partner_id=partner.id,
        direction=EndpointDirection.OUTBOUND,
        url="https://partner.example/hooks",
        event_types=[EVENT_TYPE],
        status=EndpointStatus.ACTIVE,
        sla_seconds=90,
        max_attempts=6,
    )


@pytest.fixture
def failed_delivery(partner: Partner, endpoint: PartnerEndpoint) -> Delivery:
    return Delivery(
        id=10,
        public_id=generate_uuidv7(),
        partner_id=partner.id,
        endpoint_id=endpoint.id,
        direction=EndpointDirection.OUTBOUND,
        event_type=EVENT_TYPE,
        idempotency_key="idem-replay-1",
        payload={"order_id": "ord_1"},
        payload_hash="abc",
        status=DeliveryStatus.FAILED,
        attempt_count=3,
        max_attempts=6,
        sla_deadline_at=datetime.now(UTC) + timedelta(seconds=90),
        correlation_id=str(generate_uuidv7()),
    )


def _role_token(role: str, secret: str = ADMIN_TOKEN) -> str:
    return jwt.encode({"sub": f"user-{role}", "role": role}, secret, algorithm="HS256")


@contextmanager
def _build_app(
    session: ReplaySession,
    *,
    producer: FakeKafkaProducer | None = None,
) -> Iterator[TestClient]:
    os.environ["ADMIN_BOOTSTRAP_TOKEN"] = ADMIN_TOKEN
    os.environ["KAFKA_BOOTSTRAP_SERVERS"] = "localhost:9092"
    get_settings.cache_clear()
    app = create_app()

    async def override_db() -> AsyncIterator[ReplaySession]:
        yield session

    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as client:
        client.app.state.kafka_producer = producer if producer is not None else FakeKafkaProducer()
        yield client
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _noop_kafka_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(self: object) -> None:
        return None

    monkeypatch.setattr("aiokafka.AIOKafkaProducer.start", _noop)
    monkeypatch.setattr("aiokafka.AIOKafkaProducer.stop", _noop)


def _post_replay(
    client: TestClient,
    delivery_id: uuid.UUID,
    *,
    body: dict[str, object],
    token: str | None = ADMIN_TOKEN,
) -> Any:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(
        f"/admin/v1/deliveries/{delivery_id}/replay",
        json=body,
        headers=headers,
    )


def test_empty_reason_returns_422(
    partner: Partner,
    failed_delivery: Delivery,
) -> None:
    session = ReplaySession(failed_delivery, partner)
    with _build_app(session) as client:
        for bad in ("", "   "):
            res = _post_replay(
                client,
                failed_delivery.public_id,
                body={"reason": bad},
                token=_role_token("hub_operator"),
            )
            assert res.status_code == 422, res.text


def test_viewer_cannot_replay(partner: Partner, failed_delivery: Delivery) -> None:
    session = ReplaySession(failed_delivery, partner)
    with _build_app(session) as client:
        res = _post_replay(
            client,
            failed_delivery.public_id,
            body={"reason": "partner fixed endpoint"},
            token=_role_token("hub_viewer"),
        )
        assert res.status_code == 403


def test_replay_non_failed_status_returns_409(
    partner: Partner,
    endpoint: PartnerEndpoint,
) -> None:
    pending_delivery = Delivery(
        id=11,
        public_id=generate_uuidv7(),
        partner_id=partner.id,
        endpoint_id=endpoint.id,
        direction=EndpointDirection.OUTBOUND,
        event_type=EVENT_TYPE,
        idempotency_key="idem-pending-1",
        payload={"order_id": "ord_pending"},
        payload_hash="def",
        status=DeliveryStatus.PENDING,
        attempt_count=0,
        max_attempts=6,
        sla_deadline_at=datetime.now(UTC) + timedelta(seconds=90),
        correlation_id=str(generate_uuidv7()),
    )
    session = ReplaySession(pending_delivery, partner)
    with _build_app(session) as client:
        res = _post_replay(
            client,
            pending_delivery.public_id,
            body={"reason": "should not replay pending"},
            token=_role_token("hub_operator"),
        )
        assert res.status_code == 409, res.text
        assert pending_delivery.status == DeliveryStatus.PENDING


def test_operator_can_replay(partner: Partner, failed_delivery: Delivery) -> None:
    producer = FakeKafkaProducer()
    session = ReplaySession(failed_delivery, partner)
    original_payload = dict(failed_delivery.payload)
    original_idem = failed_delivery.idempotency_key
    with _build_app(session, producer=producer) as client:
        res = _post_replay(
            client,
            failed_delivery.public_id,
            body={"reason": "partner fixed endpoint"},
            token=_role_token("hub_operator"),
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "replaying"
        assert body["delivery_id"] == str(failed_delivery.public_id)
        assert failed_delivery.status == DeliveryStatus.REPLAYING
        assert failed_delivery.payload == original_payload
        assert failed_delivery.idempotency_key == original_idem
        assert session.committed is True
        assert producer.call_count == 0
        outbox_rows = [obj for obj in session.added if isinstance(obj, OutboxEvent)]
        assert len(outbox_rows) == 1
        outbox = outbox_rows[0]
        assert outbox.topic == "hub.outbound.pending"
        assert outbox.message_key == str(partner.public_id)
        assert outbox.published_at is None
        envelope = outbox.payload
        assert envelope["payload"] == original_payload
        assert envelope["idempotency_key"] == original_idem
        assert envelope["attempt"] == 4


def test_admin_can_replay(partner: Partner, failed_delivery: Delivery) -> None:
    session = ReplaySession(failed_delivery, partner)
    with _build_app(session) as client:
        res = _post_replay(
            client,
            failed_delivery.public_id,
            body={"reason": "manual recovery"},
            token=ADMIN_TOKEN,
        )
        assert res.status_code == 200


@patch("app.domain.services.replay_service.record_delivery_metric")
def test_replay_records_hub_replay_metric(
    mock_metric: MagicMock,
    partner: Partner,
    failed_delivery: Delivery,
) -> None:
    session = ReplaySession(failed_delivery, partner)
    with _build_app(session) as client:
        res = _post_replay(
            client,
            failed_delivery.public_id,
            body={"reason": "ops ticket #42"},
            token=_role_token("hub_operator"),
        )
        assert res.status_code == 200
        mock_metric.assert_any_call(
            "hub_replay_total",
            attributes={"trigger": "manual", "partner_slug": partner.slug},
        )


def test_openapi_replay_responses_and_example() -> None:
    spec = create_app().openapi()
    post = spec["paths"]["/admin/v1/deliveries/{id}/replay"]["post"]
    assert "admin" in post["tags"]
    for code in ("401", "403", "404", "409", "422"):
        assert code in post["responses"]
    request_body = post["requestBody"]["content"]["application/json"]
    assert "schema" in request_body
    examples = request_body.get("examples", {})
    assert examples, "replay request should include OpenAPI examples"

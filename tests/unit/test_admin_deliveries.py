"""Unit tests for admin delivery list/get/attempts endpoints."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.config import get_settings
from app.domain.enums import DeliveryStatus, EndpointDirection, EndpointStatus, PartnerStatus
from app.domain.ids import generate_uuidv7
from app.domain.models.attempt import DeliveryAttempt
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.main import create_app

ADMIN_TOKEN = "test-admin-bootstrap-token-at-least-32-bytes"
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


class ListDeliveriesSession:
    def __init__(self, *, deliveries: list[Delivery], partner: Partner, total: int) -> None:
        self._rows = [(d, partner) for d in deliveries]
        self._total = total
        self._call = 0

    async def execute(self, _stmt: object) -> FakeResult:
        self._call += 1
        if self._call == 1:
            return FakeResult(scalar=self._total)
        return FakeResult(rows=self._rows)


class GetDeliverySession:
    def __init__(
        self,
        *,
        delivery: Delivery | None,
        partner: Partner,
        attempts: list[DeliveryAttempt],
    ) -> None:
        self._delivery = delivery
        self._partner = partner
        self._attempts = attempts
        self._call = 0

    async def execute(self, _stmt: object) -> FakeResult:
        self._call += 1
        if self._call == 1:
            if self._delivery is None:
                return FakeResult(row=None)
            return FakeResult(row=(self._delivery, self._partner))
        return FakeResult(rows=self._attempts)


class ListAttemptsSession:
    def __init__(
        self,
        *,
        delivery: Delivery,
        partner: Partner,
        attempts: list[DeliveryAttempt],
    ) -> None:
        self._delivery = delivery
        self._partner = partner
        self._attempts = attempts
        self._call = 0

    async def execute(self, _stmt: object) -> FakeResult:
        self._call += 1
        if self._call == 1:
            return FakeResult(row=(self._delivery, self._partner))
        if self._call == 2:
            return FakeResult(scalar=len(self._attempts))
        return FakeResult(rows=self._attempts)


@contextmanager
def _build_app(session: object) -> Iterator[TestClient]:
    os.environ["ADMIN_BOOTSTRAP_TOKEN"] = ADMIN_TOKEN
    get_settings.cache_clear()
    app = create_app()

    async def override_db() -> AsyncIterator[object]:
        yield session

    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _noop_kafka_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(self: object) -> None:
        return None

    monkeypatch.setattr("aiokafka.AIOKafkaProducer.start", _noop)
    monkeypatch.setattr("aiokafka.AIOKafkaProducer.stop", _noop)


@pytest.fixture
def partner() -> Partner:
    return Partner(
        id=1,
        public_id=generate_uuidv7(),
        slug="list-acme",
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
def delivery(partner: Partner, endpoint: PartnerEndpoint) -> Delivery:
    return Delivery(
        id=10,
        public_id=generate_uuidv7(),
        partner_id=partner.id,
        endpoint_id=endpoint.id,
        direction=EndpointDirection.OUTBOUND,
        event_type=EVENT_TYPE,
        idempotency_key="idem-list-1",
        payload={"order_id": "ord_1"},
        payload_hash="abc",
        status=DeliveryStatus.FAILED,
        attempt_count=2,
        max_attempts=6,
        sla_deadline_at=datetime.now(UTC) + timedelta(seconds=90),
        correlation_id=str(generate_uuidv7()),
        sla_breached=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def attempt(delivery: Delivery) -> DeliveryAttempt:
    return DeliveryAttempt(
        id=generate_uuidv7(),
        delivery_id=delivery.id,
        attempt_number=1,
        requested_at=datetime.now(UTC),
        responded_at=datetime.now(UTC),
        http_status_code=503,
        response_body="",
        duration_ms=120,
        created_at=datetime.now(UTC),
    )


def _auth(token: str = ADMIN_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_list_deliveries_returns_uuid_ids(
    partner: Partner,
    delivery: Delivery,
) -> None:
    session = ListDeliveriesSession(deliveries=[delivery], partner=partner, total=1)
    with _build_app(session) as client:
        res = client.get(
            "/admin/v1/deliveries",
            headers=_auth(),
            params={
                "partner_id": str(partner.public_id),
                "status": "failed",
                "limit": 10,
                "offset": 0,
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert uuid.UUID(item["id"]).version == 7
        assert item["partner_id"] == str(partner.public_id)
        assert isinstance(item["id"], str)
        assert not str(item["id"]).isdigit()


def test_get_delivery_includes_attempts(
    partner: Partner,
    delivery: Delivery,
    attempt: DeliveryAttempt,
) -> None:
    session = GetDeliverySession(delivery=delivery, partner=partner, attempts=[attempt])
    with _build_app(session) as client:
        res = client.get(
            f"/admin/v1/deliveries/{delivery.public_id}",
            headers=_auth(),
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["id"] == str(delivery.public_id)
        assert len(body["attempts"]) == 1
        assert uuid.UUID(body["attempts"][0]["id"]).version == 7


def test_get_delivery_not_found(partner: Partner) -> None:
    session = GetDeliverySession(delivery=None, partner=partner, attempts=[])
    with _build_app(session) as client:
        missing = generate_uuidv7()
        res = client.get(f"/admin/v1/deliveries/{missing}", headers=_auth())
        assert res.status_code == 404


def test_list_attempts_paginated(
    partner: Partner,
    delivery: Delivery,
    attempt: DeliveryAttempt,
) -> None:
    session = ListAttemptsSession(delivery=delivery, partner=partner, attempts=[attempt])
    with _build_app(session) as client:
        res = client.get(
            f"/admin/v1/deliveries/{delivery.public_id}/attempts",
            headers=_auth(),
            params={"limit": 20, "offset": 0},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total"] == 1
        assert body["items"][0]["attempt_number"] == 1


def test_openapi_delivery_schemas_use_uuid_not_integer() -> None:
    spec = create_app().openapi()
    schemas = spec.get("components", {}).get("schemas", {})
    for key, schema in schemas.items():
        if "Delivery" in key and "DeadLetter" not in key:
            props = schema.get("properties", {})
            for field in ("id", "delivery_id", "partner_id"):
                if field not in props:
                    continue
                assert props[field].get("type") != "integer", f"{key}.{field} must not be integer"

"""Unit tests for GET /partner/v1/deliveries/{id} (Stage 3 Task 3)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.sqlalchemy_stmt import stmt_targets_table

from app.api.deps import get_db
from app.config import get_settings
from app.domain.enums import DeliveryStatus, PartnerStatus
from app.domain.ids import generate_uuidv7
from app.domain.models.api_key import PartnerApiKey
from app.domain.models.delivery import Delivery
from app.domain.models.partner import Partner
from app.domain.services.api_keys import generate_api_key
from app.main import create_app

STATUS_READ_SCOPE = "status:read"
INBOUND_WRITE_SCOPE = "inbound:write"


class _ScalarsResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def scalars(self) -> _ScalarsResult:
        return self

    def all(self) -> list[object]:
        return self._values

    def scalar_one_or_none(self) -> object | None:
        return self._values[0] if self._values else None


class PartnerStatusSession:
    def __init__(
        self,
        *,
        api_keys: list[PartnerApiKey],
        delivery: Delivery | None,
    ) -> None:
        self._api_keys = api_keys
        self._delivery = delivery

    async def execute(self, stmt: object) -> _ScalarsResult:
        if stmt_targets_table(stmt, "partner_api_keys"):
            return _ScalarsResult(self._api_keys)
        if stmt_targets_table(stmt, "deliveries"):
            return _ScalarsResult([self._delivery] if self._delivery is not None else [])
        raise AssertionError(f"unexpected statement: {stmt!r}")


def _make_partner(*, partner_id: int = 1) -> Partner:
    return Partner(
        id=partner_id,
        public_id=generate_uuidv7(),
        slug="acme-status",
        name="ACME",
        status=PartnerStatus.ACTIVE,
        sla_seconds=60,
        rate_limit_rps=100,
        signing_secret_encrypted=b"enc",
    )


def _make_api_key_row(
    partner: Partner,
    api_key_material: tuple[str, str, str],
    *,
    scopes: list[str],
) -> PartnerApiKey:
    _full, prefix, key_hash = api_key_material
    return PartnerApiKey(
        id=generate_uuidv7(),
        partner_id=partner.id,
        key_prefix=prefix,
        key_hash=key_hash,
        scopes=scopes,
        expires_at=None,
        revoked_at=None,
        created_at=datetime.now(UTC),
    )


def _make_delivery(
    *,
    partner: Partner,
    endpoint_id: uuid.UUID | None = None,
    status: DeliveryStatus = DeliveryStatus.FAILED,
) -> Delivery:
    now = datetime.now(UTC)
    return Delivery(
        id=42,
        public_id=generate_uuidv7(),
        partner_id=partner.id,
        endpoint_id=endpoint_id or generate_uuidv7(),
        event_type="order.created",
        idempotency_key="idem-partner-status",
        payload={"order_id": "secret-ord-999", "card_number": "4111111111111111"},
        payload_hash="abc123",
        status=status.value,
        attempt_count=2,
        max_attempts=5,
        next_retry_at=None,
        first_success_at=None,
        sla_deadline_at=now + timedelta(hours=1),
        sla_breached=False,
        last_error_code="http_400",
        last_error_message="bad request",
        correlation_id=str(generate_uuidv7()),
        source_event_id="src-1",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def api_key_material() -> tuple[str, str, str]:
    return generate_api_key()


@pytest.fixture
def partner() -> Partner:
    return _make_partner()


@pytest.fixture
def other_partner() -> Partner:
    return _make_partner(partner_id=2)


@contextmanager
def _build_client(session: PartnerStatusSession) -> Iterator[TestClient]:
    os.environ["FERNET_KEY"] = "dGVzdC1mZXJuZXQta2V5LTMyLWJ5dGVzISE="
    os.environ["REDIS_URL"] = "redis://localhost:6379/0"
    os.environ["KAFKA_BOOTSTRAP_SERVERS"] = "localhost:9092"
    get_settings.cache_clear()
    app = create_app()

    async def override_db() -> AsyncIterator[PartnerStatusSession]:
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


def test_missing_auth_returns_401(
    partner: Partner,
    api_key_material: tuple[str, str, str],
) -> None:
    delivery = _make_delivery(partner=partner)
    session = PartnerStatusSession(
        api_keys=[_make_api_key_row(partner, api_key_material, scopes=[STATUS_READ_SCOPE])],
        delivery=delivery,
    )
    with _build_client(session) as client:
        res = client.get(f"/partner/v1/deliveries/{delivery.public_id}")
    assert res.status_code == 401


def test_inbound_write_only_returns_403(
    partner: Partner,
    api_key_material: tuple[str, str, str],
) -> None:
    full_key, _prefix, _hash = api_key_material
    delivery = _make_delivery(partner=partner)
    session = PartnerStatusSession(
        api_keys=[_make_api_key_row(partner, api_key_material, scopes=[INBOUND_WRITE_SCOPE])],
        delivery=delivery,
    )
    with _build_client(session) as client:
        res = client.get(
            f"/partner/v1/deliveries/{delivery.public_id}",
            headers={"Authorization": f"Bearer {full_key}"},
        )
    assert res.status_code == 403


def test_own_delivery_returns_200_without_secrets(
    partner: Partner,
    api_key_material: tuple[str, str, str],
) -> None:
    full_key, _prefix, _hash = api_key_material
    delivery = _make_delivery(partner=partner)
    session = PartnerStatusSession(
        api_keys=[_make_api_key_row(partner, api_key_material, scopes=[STATUS_READ_SCOPE])],
        delivery=delivery,
    )
    with _build_client(session) as client:
        res = client.get(
            f"/partner/v1/deliveries/{delivery.public_id}",
            headers={"Authorization": f"Bearer {full_key}"},
        )
    assert res.status_code == 200
    body: dict[str, Any] = res.json()
    assert body["id"] == str(delivery.public_id)
    assert isinstance(body["id"], str)
    assert body["status"] == "failed"
    assert body["event_type"] == "order.created"
    assert body["attempt_count"] == 2
    assert body["last_error_code"] == "http_400"
    assert body["sla_breached"] is False
    assert body["first_success_at"] is None
    assert "card_number" not in str(body)
    assert "secret-ord-999" not in str(body)
    if "payload" in body:
        assert body["payload"] == {"_masked": True}


def test_other_partner_delivery_returns_404(
    partner: Partner,
    other_partner: Partner,
    api_key_material: tuple[str, str, str],
) -> None:
    full_key, _prefix, _hash = api_key_material
    delivery = _make_delivery(partner=other_partner)
    session = PartnerStatusSession(
        api_keys=[_make_api_key_row(partner, api_key_material, scopes=[STATUS_READ_SCOPE])],
        delivery=delivery,
    )
    with _build_client(session) as client:
        res = client.get(
            f"/partner/v1/deliveries/{delivery.public_id}",
            headers={"Authorization": f"Bearer {full_key}"},
        )
    assert res.status_code == 404


def test_openapi_partner_tag_and_schema() -> None:
    spec = create_app().openapi()
    tag_names = {t["name"] for t in spec["tags"]}
    assert "partner" in tag_names
    partner_tag = next(t for t in spec["tags"] if t["name"] == "partner")
    assert partner_tag.get("description")
    assert (
        "admin" not in partner_tag["description"].lower()
        or "not admin" in partner_tag["description"].lower()
    )

    schema = spec["components"]["schemas"]["PartnerDeliveryStatusResponse"]
    assert schema["properties"]["id"]["type"] == "string"
    assert schema["properties"]["id"].get("format") == "uuid"
    for prop_name, prop in schema["properties"].items():
        assert (
            "description" in prop
        ), f"PartnerDeliveryStatusResponse.{prop_name} missing description"

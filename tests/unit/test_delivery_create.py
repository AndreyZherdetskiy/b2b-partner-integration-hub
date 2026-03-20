"""Unit tests for internal outbound delivery creation (spec §7.1.5, §7.3)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from tests.fixtures.sqlalchemy_stmt import (
    compiled_param_values,
    compiled_sql,
    is_select_stmt,
    stmt_targets_table,
)

from app.api.deps import get_db, get_now
from app.config import get_settings
from app.domain.enums import DeliveryStatus, EndpointDirection, EndpointStatus, PartnerStatus
from app.domain.ids import generate_uuidv7
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.domain.services.delivery_service import derived_idempotency_key
from app.main import create_app

FIXED_NOW = 1_720_000_000
ADMIN_TOKEN = "test-admin-bootstrap-token-at-least-32-bytes"
PARTNER_SLUG = "acme-outbound"
EVENT_TYPE = "order.created"


class _ScalarsResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def all(self) -> list[object]:
        return self._values


class _ExecuteResult:
    def __init__(
        self,
        *,
        scalar: object | None = None,
        scalars: list[object] | None = None,
    ) -> None:
        self._scalar = scalar
        self._scalars = scalars or []

    def scalar_one_or_none(self) -> object | None:
        return self._scalar

    def scalars(self) -> _ScalarsResult:
        return _ScalarsResult(self._scalars)


class FakeSession:
    def __init__(
        self,
        partner: Partner | None,
        endpoint: PartnerEndpoint | None,
    ) -> None:
        self._partner = partner
        self._endpoints = [endpoint] if endpoint is not None else []
        self._execute_calls = 0
        self.delivery_selects = 0
        self.flush_calls = 0
        self.persist_stmts: list[object] = []
        self.committed = False
        self.added: list[object] = []

    async def execute(self, stmt: object) -> _ExecuteResult:
        if stmt_targets_table(stmt, "payload_schemas"):
            return _ExecuteResult(scalar=None)
        if not is_select_stmt(stmt):
            self.persist_stmts.append(stmt)
            return _ExecuteResult()
        if stmt_targets_table(stmt, "deliveries"):
            self.delivery_selects += 1
        self._execute_calls += 1
        if self._execute_calls == 1:
            return _ExecuteResult(scalar=self._partner)
        if self._execute_calls == 2:
            return _ExecuteResult(scalars=self._endpoints)
        return _ExecuteResult(scalars=[])

    def add(self, obj: object) -> None:
        if isinstance(obj, Delivery):
            if obj.public_id is None:
                obj.public_id = generate_uuidv7()
            if obj.id is None:
                obj.id = len(self.added) + 1
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_calls += 1
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.added.clear()


class IntegrityRetrySession(FakeSession):
    def __init__(
        self,
        partner: Partner | None,
        endpoint: PartnerEndpoint | None,
        *,
        existing_delivery: Delivery,
    ) -> None:
        super().__init__(partner, endpoint)
        self._existing_delivery = existing_delivery
        self._flush_attempted = False

    async def flush(self) -> None:
        if not self._flush_attempted:
            self._flush_attempted = True
            raise IntegrityError("duplicate", {}, Exception())

    async def execute(self, stmt: object) -> _ExecuteResult:
        if stmt_targets_table(stmt, "payload_schemas"):
            return _ExecuteResult(scalar=None)
        if not is_select_stmt(stmt):
            raise IntegrityError("duplicate", {}, Exception())
        if stmt_targets_table(stmt, "deliveries"):
            self.delivery_selects += 1
        self._execute_calls += 1
        if self._execute_calls == 1:
            return _ExecuteResult(scalar=self._partner)
        if self._execute_calls == 2:
            return _ExecuteResult(scalars=self._endpoints)
        return _ExecuteResult(scalars=[self._existing_delivery])


class FakeKafkaProducer:
    def __init__(self, *, fail: bool = False) -> None:
        self.messages: list[tuple[str, str | None, dict[str, Any], list[tuple[str, bytes]]]] = []
        self.fail = fail
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
        if self.fail:
            raise RuntimeError("kafka unavailable")
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
        event_types=[EVENT_TYPE, "order.updated"],
        status=EndpointStatus.ACTIVE,
        sla_seconds=90,
        max_attempts=6,
    )


@contextmanager
def _build_app(
    session: FakeSession,
    *,
    producer: FakeKafkaProducer | None = None,
    now: int = FIXED_NOW,
) -> Iterator[TestClient]:
    os.environ["ADMIN_BOOTSTRAP_TOKEN"] = ADMIN_TOKEN
    os.environ["KAFKA_BOOTSTRAP_SERVERS"] = "localhost:9092"
    get_settings.cache_clear()
    app = create_app()

    async def override_db() -> AsyncIterator[FakeSession]:
        yield session

    async def override_now() -> int:
        return now

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_now] = override_now

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


def _post_outbound(
    client: TestClient,
    *,
    partner: Partner,
    idempotency_key: str = "out-idem-1",
    correlation_id: str | None = None,
    token: str | None = ADMIN_TOKEN,
) -> Any:
    body: dict[str, object] = {
        "partner_id": str(partner.public_id),
        "event_type": EVENT_TYPE,
        "payload": {"order_id": "ord_99"},
        "idempotency_key": idempotency_key,
    }
    if correlation_id is not None:
        body["correlation_id"] = correlation_id
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/internal/v1/outbound/events", json=body, headers=headers)


def test_missing_auth_returns_401(partner: Partner, endpoint: PartnerEndpoint) -> None:
    session = FakeSession(partner, endpoint)
    with _build_app(session) as client:
        res = _post_outbound(client, partner=partner, token=None)
        assert res.status_code == 401


def test_unknown_partner_returns_404(endpoint: PartnerEndpoint) -> None:
    missing = Partner(
        id=99,
        public_id=generate_uuidv7(),
        slug="missing",
        name="Missing",
        status=PartnerStatus.ACTIVE,
        sla_seconds=60,
        rate_limit_rps=100,
        signing_secret_encrypted=None,
    )
    session = FakeSession(None, endpoint)
    with _build_app(session) as client:
        res = _post_outbound(client, partner=missing)
        assert res.status_code == 404


def test_inactive_partner_returns_422(partner: Partner, endpoint: PartnerEndpoint) -> None:
    partner.status = PartnerStatus.SUSPENDED
    session = FakeSession(partner, endpoint)
    with _build_app(session) as client:
        res = _post_outbound(client, partner=partner)
        assert res.status_code == 422


def test_no_matching_endpoint_returns_422(partner: Partner) -> None:
    session = FakeSession(partner, None)
    with _build_app(session) as client:
        res = _post_outbound(client, partner=partner)
        assert res.status_code == 422


def test_unique_key_enqueue_does_not_select_deliveries_before_insert(
    partner: Partner,
    endpoint: PartnerEndpoint,
) -> None:
    session = FakeSession(partner, endpoint)
    with _build_app(session) as client:
        res = _post_outbound(client, partner=partner, idempotency_key="insert-first-key")
        assert res.status_code == 202, res.text
        assert session.delivery_selects == 0


def test_accepted_persist_skips_flush_and_uses_one_statement(
    partner: Partner,
    endpoint: PartnerEndpoint,
) -> None:
    session = FakeSession(partner, endpoint)
    with _build_app(session) as client:
        res = _post_outbound(client, partner=partner, idempotency_key="one-rtt-key")
        assert res.status_code == 202, res.text
        assert session.flush_calls == 0
        assert len(session.persist_stmts) == 1
        sql = str(session.persist_stmts[0].compile()).lower()
        assert "with" in sql
        assert "deliveries" in sql
        assert "outbox_events" in sql


def test_accepted_returns_202_and_creates_unpublished_outbox(
    partner: Partner,
    endpoint: PartnerEndpoint,
) -> None:
    producer = FakeKafkaProducer()
    session = FakeSession(partner, endpoint)
    correlation_id = str(generate_uuidv7())
    with _build_app(session, producer=producer) as client:
        res = _post_outbound(
            client,
            partner=partner,
            correlation_id=correlation_id,
        )
        assert res.status_code == 202, res.text
        payload = res.json()
        assert payload["status"] == "accepted"
        assert uuid.UUID(payload["delivery_id"]).version == 7
        assert len(payload["delivery_ids"]) == 1
        assert payload["delivery_id"] == payload["delivery_ids"][0]
        assert session.committed is True
        assert producer.call_count == 0
        assert len(session.persist_stmts) == 1
        sql = compiled_sql(session.persist_stmts[0])
        assert "with" in sql
        assert "insert into deliveries" in sql.replace('"', "")
        assert "outbox_events" in sql
        params = compiled_param_values(session.persist_stmts[0])
        assert "hub.outbound.pending" in params
        assert str(partner.public_id) in params
        assert str(endpoint.id) in params
        assert EVENT_TYPE in params
        assert correlation_id in params
        assert "out-idem-1" in params
        assert derived_idempotency_key("out-idem-1", endpoint.id) in params


def test_duplicate_idempotency_returns_200_without_second_publish(
    partner: Partner,
    endpoint: PartnerEndpoint,
) -> None:
    existing_public_id = generate_uuidv7()
    existing = Delivery(
        id=10,
        public_id=existing_public_id,
        partner_id=partner.id,
        endpoint_id=endpoint.id,
        direction=EndpointDirection.OUTBOUND,
        event_type=EVENT_TYPE,
        idempotency_key=derived_idempotency_key("dup-key", endpoint.id),
        source_event_id="dup-key",
        payload={"order_id": "ord_dup"},
        payload_hash="abc123",
        status=DeliveryStatus.PENDING,
        attempt_count=0,
        max_attempts=6,
        sla_deadline_at=datetime.now(UTC) + timedelta(seconds=90),
        correlation_id=str(generate_uuidv7()),
    )
    producer = FakeKafkaProducer()
    session = IntegrityRetrySession(partner, endpoint, existing_delivery=existing)
    with _build_app(session, producer=producer) as client:
        res = _post_outbound(client, partner=partner, idempotency_key="dup-key")
        assert res.status_code == 200, res.text
        payload = res.json()
        assert payload["status"] == "duplicate"
        assert payload["delivery_id"] == str(existing_public_id)
        assert payload["delivery_ids"] == [str(existing_public_id)]
        assert producer.call_count == 0


def test_kafka_unavailable_producer_still_returns_202_with_outbox(
    partner: Partner,
    endpoint: PartnerEndpoint,
) -> None:
    producer = FakeKafkaProducer(fail=True)
    session = FakeSession(partner, endpoint)
    with _build_app(session, producer=producer) as client:
        res = _post_outbound(client, partner=partner, idempotency_key="kafka-fail-key")
        assert res.status_code == 202, res.text
        payload = res.json()
        assert payload["status"] == "accepted"
        assert uuid.UUID(payload["delivery_id"]).version == 7
        assert len(payload["delivery_ids"]) == 1
        assert session.committed is True
        assert producer.call_count == 0
        assert len(session.persist_stmts) == 1
        sql = compiled_sql(session.persist_stmts[0])
        assert "outbox_events" in sql
        assert "hub.outbound.pending" in compiled_param_values(session.persist_stmts[0])


def test_body_correlation_id_must_be_uuidv7(partner: Partner, endpoint: PartnerEndpoint) -> None:
    session = FakeSession(partner, endpoint)
    with _build_app(session) as client:
        res = _post_outbound(client, partner=partner, correlation_id=str(uuid.uuid4()))
        assert res.status_code == 422


def test_openapi_internal_outbound_responses_and_examples() -> None:
    spec = create_app().openapi()
    post = spec["paths"]["/internal/v1/outbound/events"]["post"]
    assert "internal" in post["tags"]
    for code in ("401", "403", "404", "409", "422"):
        assert code in post["responses"]
    assert "200" in post["responses"]
    assert "202" in post["responses"]
    request_body = post["requestBody"]["content"]["application/json"]
    assert "order_created" in request_body["examples"]

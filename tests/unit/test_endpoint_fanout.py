"""Unit tests for multi-endpoint event_type fan-out (Stage 3 Task 0)."""

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
from app.domain.enums import (
    DeliveryDirection,
    DeliveryStatus,
    EndpointDirection,
    EndpointStatus,
    PartnerStatus,
)
from app.domain.ids import generate_uuidv7
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.domain.services.delivery_service import derived_idempotency_key
from app.main import create_app

FIXED_NOW = 1_720_000_000
ADMIN_TOKEN = "test-admin-bootstrap-token"
EVENT_TYPE = "order.created"
CLIENT_IDEM_KEY = "fanout-idem-1"


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


class FanoutFakeSession:
    def __init__(
        self,
        partner: Partner | None,
        endpoints: list[PartnerEndpoint],
    ) -> None:
        self._partner = partner
        self._endpoints = endpoints
        self._execute_calls = 0
        self.committed = False
        self.persist_stmts: list[object] = []
        self.added: list[object] = []

    async def execute(self, stmt: object) -> _ExecuteResult:
        if stmt_targets_table(stmt, "payload_schemas"):
            return _ExecuteResult(scalar=None)
        if not is_select_stmt(stmt):
            self.persist_stmts.append(stmt)
            return _ExecuteResult()
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
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.added.clear()


class FanoutIntegrityRetrySession(FanoutFakeSession):
    def __init__(
        self,
        partner: Partner | None,
        endpoints: list[PartnerEndpoint],
        *,
        existing_deliveries: list[Delivery],
    ) -> None:
        super().__init__(partner, endpoints)
        self._existing_deliveries = existing_deliveries
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
        self._execute_calls += 1
        if self._execute_calls == 1:
            return _ExecuteResult(scalar=self._partner)
        if self._execute_calls == 2:
            return _ExecuteResult(scalars=self._endpoints)
        if self._execute_calls == 3:
            return _ExecuteResult(scalars=self._existing_deliveries)
        return _ExecuteResult(scalars=[])


class FakeKafkaProducer:
    def __init__(self) -> None:
        self.call_count = 0

    async def send_and_wait(self, *args: object, **kwargs: object) -> object:
        self.call_count += 1
        return MagicMock()


@pytest.fixture
def partner() -> Partner:
    return Partner(
        id=1,
        public_id=generate_uuidv7(),
        slug="fanout-partner",
        name="Fanout Partner",
        status=PartnerStatus.ACTIVE,
        sla_seconds=120,
        rate_limit_rps=100,
        signing_secret_encrypted=None,
    )


def _endpoint(
    partner: Partner,
    *,
    url: str,
    event_types: list[str],
) -> PartnerEndpoint:
    return PartnerEndpoint(
        id=generate_uuidv7(),
        partner_id=partner.id,
        direction=EndpointDirection.OUTBOUND,
        url=url,
        event_types=event_types,
        status=EndpointStatus.ACTIVE,
        sla_seconds=90,
        max_attempts=6,
    )


@contextmanager
def _build_app(session: FanoutFakeSession) -> Iterator[TestClient]:
    os.environ["ADMIN_BOOTSTRAP_TOKEN"] = ADMIN_TOKEN
    os.environ["KAFKA_BOOTSTRAP_SERVERS"] = "localhost:9092"
    get_settings.cache_clear()
    app = create_app()

    async def override_db() -> AsyncIterator[FanoutFakeSession]:
        yield session

    async def override_now() -> int:
        return FIXED_NOW

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_now] = override_now

    with TestClient(app) as client:
        client.app.state.kafka_producer = FakeKafkaProducer()
        yield client
    get_settings.cache_clear()


def _post_outbound(
    client: TestClient,
    *,
    partner: Partner,
    event_type: str = EVENT_TYPE,
    idempotency_key: str = CLIENT_IDEM_KEY,
) -> Any:
    body = {
        "partner_id": str(partner.public_id),
        "event_type": event_type,
        "payload": {"order_id": "ord_fanout"},
        "idempotency_key": idempotency_key,
        "correlation_id": str(generate_uuidv7()),
    }
    return client.post(
        "/internal/v1/outbound/events",
        json=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ADMIN_TOKEN}",
        },
    )


@pytest.fixture(autouse=True)
def _noop_kafka_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(self: object) -> None:
        return None

    monkeypatch.setattr("aiokafka.AIOKafkaProducer.start", _noop)
    monkeypatch.setattr("aiokafka.AIOKafkaProducer.stop", _noop)


def test_derived_idempotency_key_includes_endpoint_uuid() -> None:
    endpoint_id = uuid.UUID("0194a2b3-c4d5-7890-abcd-ef1234567890")
    assert derived_idempotency_key("idem-1", endpoint_id) == (
        "idem-1::0194a2b3-c4d5-7890-abcd-ef1234567890"
    )


def test_two_matching_endpoints_create_two_deliveries_and_outbox_rows(
    partner: Partner,
) -> None:
    ep1 = _endpoint(partner, url="https://a.example/hook", event_types=[EVENT_TYPE])
    ep2 = _endpoint(partner, url="https://b.example/hook", event_types=[EVENT_TYPE])
    session = FanoutFakeSession(partner, [ep1, ep2])

    with _build_app(session) as client:
        res = _post_outbound(client, partner=partner)
        assert res.status_code == 202, res.text
        payload = res.json()
        assert payload["status"] == "accepted"
        assert len(payload["delivery_ids"]) == 2
        assert payload["delivery_id"] == payload["delivery_ids"][0]
        for delivery_id in payload["delivery_ids"]:
            assert uuid.UUID(delivery_id).version == 7

        assert len(session.persist_stmts) == 1
        sql = compiled_sql(session.persist_stmts[0])
        assert "with" in sql
        assert "outbox_events" in sql
        params = compiled_param_values(session.persist_stmts[0])
        assert str(ep1.id) in params
        assert str(ep2.id) in params
        assert CLIENT_IDEM_KEY in params
        assert derived_idempotency_key(CLIENT_IDEM_KEY, ep1.id) in params
        assert derived_idempotency_key(CLIENT_IDEM_KEY, ep2.id) in params
        assert session.committed is True


def test_endpoint_without_event_type_not_selected(partner: Partner) -> None:
    matching = _endpoint(partner, url="https://match.example/hook", event_types=[EVENT_TYPE])
    non_matching = _endpoint(
        partner,
        url="https://other.example/hook",
        event_types=["order.updated"],
    )
    session = FanoutFakeSession(partner, [matching])

    with _build_app(session) as client:
        res = _post_outbound(client, partner=partner)
        assert res.status_code == 202, res.text
        params = compiled_param_values(session.persist_stmts[0])
        assert str(matching.id) in params
        assert str(non_matching.id) not in params


def test_repeat_caller_key_returns_200_without_extra_rows(partner: Partner) -> None:
    ep = _endpoint(partner, url="https://a.example/hook", event_types=[EVENT_TYPE])
    existing_public_id = generate_uuidv7()
    existing = Delivery(
        id=10,
        public_id=existing_public_id,
        partner_id=partner.id,
        endpoint_id=ep.id,
        direction=DeliveryDirection.OUTBOUND,
        event_type=EVENT_TYPE,
        idempotency_key=derived_idempotency_key(CLIENT_IDEM_KEY, ep.id),
        source_event_id=CLIENT_IDEM_KEY,
        payload={"order_id": "ord_dup"},
        payload_hash="abc123",
        status=DeliveryStatus.PENDING,
        attempt_count=0,
        max_attempts=6,
        sla_deadline_at=datetime.now(UTC) + timedelta(seconds=90),
        correlation_id=str(generate_uuidv7()),
    )
    session = FanoutIntegrityRetrySession(partner, [ep], existing_deliveries=[existing])

    with _build_app(session) as client:
        res = _post_outbound(client, partner=partner)
        assert res.status_code == 200, res.text
        payload = res.json()
        assert payload["status"] == "duplicate"
        assert payload["delivery_ids"] == [str(existing_public_id)]
        assert payload["delivery_id"] == str(existing_public_id)
        deliveries = [obj for obj in session.added if isinstance(obj, Delivery)]
        assert len(deliveries) == 0
        assert session.committed is False

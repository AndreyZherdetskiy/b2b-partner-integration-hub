"""Unit tests for admin sandbox test delivery (POST /admin/v1/deliveries/test)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
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
PARTNER_SLUG = "sandbox-acme"
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
        return None


def _role_token(role: str, secret: str = ADMIN_TOKEN) -> str:
    return jwt.encode({"sub": f"user-{role}", "role": role}, secret, algorithm="HS256")


@contextmanager
def _build_app(
    session: FakeSession,
    *,
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


def _post_sandbox(
    client: TestClient,
    *,
    partner: Partner,
    token: str,
    idempotency_key: str | None = None,
    payload: dict[str, object] | None = None,
) -> Any:
    body: dict[str, object] = {
        "partner_id": str(partner.public_id),
        "event_type": EVENT_TYPE,
        "payload": payload or {"order_id": "ord_sandbox"},
    }
    if idempotency_key is not None:
        body["idempotency_key"] = idempotency_key
    return client.post(
        "/admin/v1/deliveries/test",
        json=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )


def test_sandbox_accepted_returns_202_creates_delivery_and_outbox(
    partner: Partner,
    endpoint: PartnerEndpoint,
) -> None:
    session = FakeSession(partner, endpoint)
    with _build_app(session) as client:
        res = _post_sandbox(client, partner=partner, token=ADMIN_TOKEN)
        assert res.status_code == 202, res.text
        body = res.json()
        assert body["status"] == "accepted"
        assert uuid.UUID(body["delivery_id"]).version == 7
        assert len(body["delivery_ids"]) == 1
        assert session.committed is True
        assert len(session.persist_stmts) == 1
        sql = compiled_sql(session.persist_stmts[0])
        assert "with" in sql
        assert "outbox_events" in sql
        params = compiled_param_values(session.persist_stmts[0])
        assert "hub.outbound.pending" in params
        assert str(partner.public_id) in params
        assert DeliveryStatus.PENDING in params or "pending" in params


def test_sandbox_viewer_returns_403(partner: Partner, endpoint: PartnerEndpoint) -> None:
    session = FakeSession(partner, endpoint)
    with _build_app(session) as client:
        res = _post_sandbox(client, partner=partner, token=_role_token("hub_viewer"))
        assert res.status_code == 403


def test_sandbox_response_has_no_bigint_ids(
    partner: Partner,
    endpoint: PartnerEndpoint,
) -> None:
    session = FakeSession(partner, endpoint)
    with _build_app(session) as client:
        res = _post_sandbox(client, partner=partner, token=ADMIN_TOKEN)
        assert res.status_code == 202
        text = res.text
        assert '"id":' not in text or "integer" not in text
        body = res.json()
        for key in ("delivery_id", "delivery_ids"):
            val = body[key]
            if isinstance(val, list):
                for item in val:
                    assert isinstance(item, str)
                    assert uuid.UUID(item).version == 7
            else:
                assert isinstance(val, str)
                assert uuid.UUID(val).version == 7


def test_sandbox_generates_idempotency_key_when_omitted(
    partner: Partner,
    endpoint: PartnerEndpoint,
) -> None:
    session = FakeSession(partner, endpoint)
    with _build_app(session) as client:
        res = _post_sandbox(client, partner=partner, token=ADMIN_TOKEN)
        assert res.status_code == 202
        params = compiled_param_values(session.persist_stmts[0])
        source_ids = [p for p in params if p.startswith("sandbox::") and p.count("::") == 1]
        assert len(source_ids) == 1
        assert derived_idempotency_key(source_ids[0], endpoint.id) in params


def test_openapi_sandbox_test_delivery_path_and_fields() -> None:
    spec = create_app().openapi()
    assert "/admin/v1/deliveries/test" in spec["paths"]
    post = spec["paths"]["/admin/v1/deliveries/test"]["post"]
    assert "admin" in post["tags"]
    assert "202" in post["responses"]
    request_body = post["requestBody"]["content"]["application/json"]
    schema_ref = request_body["schema"]["$ref"]
    schema_name = schema_ref.split("/")[-1]
    schema = spec["components"]["schemas"][schema_name]
    for field in ("partner_id", "event_type", "payload", "idempotency_key", "correlation_id"):
        assert field in schema["properties"]
        assert "description" in schema["properties"][field]
    assert "/admin/v1/replay-approvals" in spec["paths"]
    get_list = spec["paths"]["/admin/v1/replay-approvals"]["get"]
    assert "200" in get_list["responses"]

"""Unit tests for DLQ acknowledge and purge admin endpoints."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api.deps import get_db
from app.config import get_settings
from app.domain.enums import DeadLetterReason, DeliveryStatus, EndpointDirection, PartnerStatus
from app.domain.ids import generate_uuidv7
from app.domain.models.audit import AuditLog
from app.domain.models.dead_letter import DeadLetter
from app.domain.models.delivery import Delivery
from app.domain.models.partner import Partner
from app.main import create_app

ADMIN_TOKEN = "test-admin-bootstrap-token-at-least-32-bytes"


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


class DlqSession:
    def __init__(
        self,
        *,
        dead_letter: DeadLetter | None,
        delivery: Delivery | None,
        partner: Partner | None,
    ) -> None:
        self._dead_letter = dead_letter
        self._delivery = delivery
        self._partner = partner
        self.added: list[object] = []
        self.deleted: list[object] = []
        self.committed = False

    async def execute(self, _stmt: object) -> FakeResult:
        if self._dead_letter is None:
            return FakeResult(row=None)
        return FakeResult(row=(self._dead_letter, self._delivery, self._partner))

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def delete(self, obj: object) -> None:
        self.deleted.append(obj)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, _obj: object) -> None:
        return None


def _role_token(role: str, secret: str = ADMIN_TOKEN) -> str:
    return jwt.encode({"sub": f"user-{role}", "role": role}, secret, algorithm="HS256")


@contextmanager
def _build_app(session: DlqSession) -> Iterator[TestClient]:
    os.environ["ADMIN_BOOTSTRAP_TOKEN"] = ADMIN_TOKEN
    get_settings.cache_clear()
    app = create_app()

    async def override_db() -> AsyncIterator[DlqSession]:
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
        slug="dlq-acme",
        name="ACME",
        status=PartnerStatus.ACTIVE,
        sla_seconds=120,
        rate_limit_rps=100,
        signing_secret_encrypted=None,
    )


@pytest.fixture
def delivery(partner: Partner) -> Delivery:
    return Delivery(
        id=42,
        public_id=generate_uuidv7(),
        partner_id=partner.id,
        endpoint_id=generate_uuidv7(),
        direction=EndpointDirection.OUTBOUND,
        event_type="order.created",
        idempotency_key="idem-dlq",
        payload={"order_id": "ord_dlq"},
        payload_hash="abc",
        status=DeliveryStatus.FAILED,
        attempt_count=5,
        max_attempts=6,
        sla_deadline_at=datetime.now(UTC),
        correlation_id=str(generate_uuidv7()),
    )


@pytest.fixture
def dead_letter(partner: Partner, delivery: Delivery) -> DeadLetter:
    return DeadLetter(
        id=generate_uuidv7(),
        delivery_id=delivery.id,
        partner_id=partner.id,
        reason=DeadLetterReason.MAX_ATTEMPTS_EXCEEDED.value,
        last_http_status=503,
        last_error_message="upstream timeout",
        acknowledged_at=None,
        acknowledged_by=None,
    )


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _post_ack(client: TestClient, dlq_id: uuid.UUID, *, token: str) -> Any:
    return client.post(
        f"/admin/v1/dead-letters/{dlq_id}/ack",
        headers=_auth_headers(token),
    )


def _delete_purge(
    client: TestClient,
    dlq_id: uuid.UUID,
    *,
    body: dict[str, str],
    token: str,
) -> Any:
    return client.request(
        "DELETE",
        f"/admin/v1/dead-letters/{dlq_id}",
        json=body,
        headers={**_auth_headers(token), "Content-Type": "application/json"},
    )


def test_viewer_ack_returns_403(dead_letter: DeadLetter) -> None:
    session = DlqSession(dead_letter=dead_letter, delivery=None, partner=None)
    with _build_app(session) as client:
        res = _post_ack(client, dead_letter.id, token=_role_token("hub_viewer"))
        assert res.status_code == 403


def test_operator_ack_sets_fields(
    dead_letter: DeadLetter,
    delivery: Delivery,
    partner: Partner,
) -> None:
    session = DlqSession(dead_letter=dead_letter, delivery=delivery, partner=partner)
    with _build_app(session) as client:
        res = _post_ack(client, dead_letter.id, token=_role_token("hub_operator"))
        assert res.status_code == 200, res.text
        assert dead_letter.acknowledged_at is not None
        assert dead_letter.acknowledged_by == "user-hub_operator"
        audits = [obj for obj in session.added if isinstance(obj, AuditLog)]
        assert len(audits) == 1
        assert audits[0].action == "dlq.ack"
        assert audits[0].resource_type == "dead_letter"
        assert audits[0].resource_id == dead_letter.id


def test_second_ack_returns_409(
    dead_letter: DeadLetter,
    delivery: Delivery,
    partner: Partner,
) -> None:
    dead_letter.acknowledged_at = datetime.now(UTC)
    dead_letter.acknowledged_by = "prior-user"
    session = DlqSession(dead_letter=dead_letter, delivery=delivery, partner=partner)
    with _build_app(session) as client:
        res = _post_ack(client, dead_letter.id, token=_role_token("hub_operator"))
        assert res.status_code == 409


def test_viewer_purge_returns_403(dead_letter: DeadLetter) -> None:
    session = DlqSession(dead_letter=dead_letter, delivery=None, partner=None)
    with _build_app(session) as client:
        res = _delete_purge(
            client,
            dead_letter.id,
            body={"reason": "false positive"},
            token=_role_token("hub_viewer"),
        )
        assert res.status_code == 403


def test_operator_purge_returns_403(dead_letter: DeadLetter) -> None:
    session = DlqSession(dead_letter=dead_letter, delivery=None, partner=None)
    with _build_app(session) as client:
        res = _delete_purge(
            client,
            dead_letter.id,
            body={"reason": "false positive"},
            token=_role_token("hub_operator"),
        )
        assert res.status_code == 403


def test_admin_purge_sets_manual_purge_and_keeps_delivery(
    dead_letter: DeadLetter,
    delivery: Delivery,
    partner: Partner,
) -> None:
    session = DlqSession(dead_letter=dead_letter, delivery=delivery, partner=partner)
    with _build_app(session) as client:
        res = _delete_purge(
            client,
            dead_letter.id,
            body={"reason": "contract fixed upstream"},
            token=ADMIN_TOKEN,
        )
        assert res.status_code == 200, res.text
        assert dead_letter.reason == DeadLetterReason.MANUAL_PURGE.value
        delivery_deletes = [obj for obj in session.deleted if isinstance(obj, Delivery)]
        assert len(delivery_deletes) == 0
        assert any(isinstance(stmt, type(delete(Delivery))) for stmt in session.deleted) is False
        audits = [obj for obj in session.added if isinstance(obj, AuditLog)]
        assert len(audits) == 1
        assert audits[0].action == "dlq.purge"
        assert audits[0].resource_id == dead_letter.id
        assert audits[0].metadata_.get("reason") == "contract fixed upstream"


def test_purge_empty_reason_returns_422(dead_letter: DeadLetter) -> None:
    session = DlqSession(dead_letter=dead_letter, delivery=None, partner=None)
    with _build_app(session) as client:
        for bad in ("", "   "):
            res = _delete_purge(client, dead_letter.id, body={"reason": bad}, token=ADMIN_TOKEN)
            assert res.status_code == 422

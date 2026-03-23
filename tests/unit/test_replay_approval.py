"""Unit tests for replay approval flow (Stage 3)."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.config import get_settings
from app.domain.enums import (
    DeliveryStatus,
    EndpointDirection,
    EndpointStatus,
    PartnerStatus,
    ReplayApprovalStatus,
)
from app.domain.ids import generate_uuidv7
from app.domain.models.audit import AuditLog
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.outbox import OutboxEvent
from app.domain.models.partner import Partner
from app.domain.models.replay_approval import ReplayApproval
from app.main import create_app

ADMIN_TOKEN = "test-admin-bootstrap-token-at-least-32-bytes"
PARTNER_SLUG = "approval-acme"
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

    async def refresh(self, obj: object) -> None:
        if isinstance(obj, ReplayApproval) and obj.id is None:
            object.__setattr__(obj, "id", generate_uuidv7())


class ApprovalFlowSession(ReplaySession):
    def __init__(
        self,
        delivery: Delivery,
        partner: Partner,
        approval: ReplayApproval,
    ) -> None:
        super().__init__(delivery, partner)
        self._approval = approval

    async def execute(self, stmt: object) -> FakeResult:
        if "replay_approvals" in str(stmt).lower():
            return FakeResult(row=(self._approval, self._delivery, self._partner))
        return await super().execute(stmt)


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
        return None


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
        idempotency_key="idem-approval-1",
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
    approval_required: bool | None = None,
    producer: FakeKafkaProducer | None = None,
) -> Iterator[TestClient]:
    os.environ["ADMIN_BOOTSTRAP_TOKEN"] = ADMIN_TOKEN
    os.environ["KAFKA_BOOTSTRAP_SERVERS"] = "localhost:9092"
    if approval_required is not None:
        os.environ["HUB_REPLAY_APPROVAL_REQUIRED"] = "true" if approval_required else "false"
    else:
        os.environ.pop("HUB_REPLAY_APPROVAL_REQUIRED", None)
    get_settings.cache_clear()
    app = create_app()

    async def override_db() -> AsyncIterator[ReplaySession]:
        yield session

    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as client:
        client.app.state.kafka_producer = producer if producer is not None else FakeKafkaProducer()
        yield client
    if approval_required is not None:
        os.environ.pop("HUB_REPLAY_APPROVAL_REQUIRED", None)
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
    token: str,
) -> Any:
    return client.post(
        f"/admin/v1/deliveries/{delivery_id}/replay",
        json=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )


def test_setting_false_replay_still_immediate(partner: Partner, failed_delivery: Delivery) -> None:
    session = ReplaySession(failed_delivery, partner)
    with _build_app(session, approval_required=False) as client:
        res = _post_replay(
            client,
            failed_delivery.public_id,
            body={"reason": "partner fixed endpoint"},
            token=_role_token("hub_operator"),
        )
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "replaying"
        assert failed_delivery.status == DeliveryStatus.REPLAYING


def test_setting_true_operator_replay_creates_pending(
    partner: Partner,
    failed_delivery: Delivery,
) -> None:
    session = ReplaySession(failed_delivery, partner)
    with _build_app(session, approval_required=True) as client:
        res = _post_replay(
            client,
            failed_delivery.public_id,
            body={"reason": "needs admin sign-off"},
            token=_role_token("hub_operator"),
        )
        assert res.status_code == 202, res.text
        body = res.json()
        assert body["status"] == "pending"
        assert uuid.UUID(body["approval_id"])
        assert failed_delivery.status == DeliveryStatus.FAILED
        assert session.committed is True
        approvals = [obj for obj in session.added if isinstance(obj, ReplayApproval)]
        assert len(approvals) == 1
        assert approvals[0].status == ReplayApprovalStatus.PENDING.value
        outbox_rows = [obj for obj in session.added if isinstance(obj, OutboxEvent)]
        assert outbox_rows == []


def test_viewer_cannot_approve(partner: Partner, failed_delivery: Delivery) -> None:
    approval = ReplayApproval(
        id=generate_uuidv7(),
        delivery_id=failed_delivery.id,
        reason="ops ticket",
        requested_by="operator-1",
        status=ReplayApprovalStatus.PENDING.value,
    )
    session = ApprovalFlowSession(failed_delivery, partner, approval)
    with _build_app(session, approval_required=True) as client:
        res = client.post(
            f"/admin/v1/replay-approvals/{approval.id}/approve",
            headers={"Authorization": f"Bearer {_role_token('hub_viewer')}"},
        )
        assert res.status_code == 403


def test_admin_approve_replays_delivery(
    partner: Partner,
    failed_delivery: Delivery,
) -> None:
    approval = ReplayApproval(
        id=generate_uuidv7(),
        delivery_id=failed_delivery.id,
        reason="ops ticket",
        requested_by="operator-1",
        status=ReplayApprovalStatus.PENDING.value,
    )
    session = ApprovalFlowSession(failed_delivery, partner, approval)
    original_payload = dict(failed_delivery.payload)
    with _build_app(session, approval_required=True) as client:
        res = client.post(
            f"/admin/v1/replay-approvals/{approval.id}/approve",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "replaying"
        assert body["delivery_id"] == str(failed_delivery.public_id)
        assert failed_delivery.status == DeliveryStatus.REPLAYING
        assert failed_delivery.payload == original_payload
        assert approval.status == ReplayApprovalStatus.APPROVED.value
        assert approval.approved_by == "bootstrap"
        outbox_rows = [obj for obj in session.added if isinstance(obj, OutboxEvent)]
        assert len(outbox_rows) == 1
        audits = [obj for obj in session.added if isinstance(obj, AuditLog)]
        actions = {a.action for a in audits}
        assert "delivery.replay" in actions
        assert "replay.approve" in actions


def test_empty_reason_on_replay_returns_422(
    partner: Partner,
    failed_delivery: Delivery,
) -> None:
    session = ReplaySession(failed_delivery, partner)
    with _build_app(session, approval_required=True) as client:
        for bad in ("", "   "):
            res = _post_replay(
                client,
                failed_delivery.public_id,
                body={"reason": bad},
                token=_role_token("hub_operator"),
            )
            assert res.status_code == 422, res.text


def test_empty_reason_on_reject_returns_422(
    partner: Partner,
    failed_delivery: Delivery,
) -> None:
    approval = ReplayApproval(
        id=generate_uuidv7(),
        delivery_id=failed_delivery.id,
        reason="ops ticket",
        requested_by="operator-1",
        status=ReplayApprovalStatus.PENDING.value,
    )
    session = ApprovalFlowSession(failed_delivery, partner, approval)
    with _build_app(session, approval_required=True) as client:
        for bad in ("", "   "):
            res = client.post(
                f"/admin/v1/replay-approvals/{approval.id}/reject",
                json={"reason": bad},
                headers={
                    "Authorization": f"Bearer {ADMIN_TOKEN}",
                    "Content-Type": "application/json",
                },
            )
            assert res.status_code == 422, res.text


class ListApprovalsSession:
    def __init__(
        self,
        approvals: list[tuple[ReplayApproval, Delivery, Partner]],
        *,
        total: int | None = None,
    ) -> None:
        self._approvals = approvals
        self._total = total if total is not None else len(approvals)
        self._execute_calls = 0

    async def execute(self, stmt: object) -> FakeResult:
        self._execute_calls += 1
        if self._execute_calls == 1:
            return FakeResult(scalar=self._total)
        return FakeResult(rows=list(self._approvals))


def test_list_replay_approvals_defaults_to_pending(
    partner: Partner,
    failed_delivery: Delivery,
) -> None:
    approval = ReplayApproval(
        id=generate_uuidv7(),
        delivery_id=failed_delivery.id,
        reason="ops ticket",
        requested_by="operator-1",
        status=ReplayApprovalStatus.PENDING.value,
        created_at=datetime.now(UTC),
    )
    session = ListApprovalsSession([(approval, failed_delivery, partner)])
    with _build_app(session, approval_required=True) as client:
        res = client.get(
            "/admin/v1/replay-approvals",
            headers={"Authorization": f"Bearer {_role_token('hub_viewer')}"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["id"] == str(approval.id)
        assert item["delivery_id"] == str(failed_delivery.public_id)
        assert item["status"] == "pending"
        assert item["reason"] == "ops ticket"
        assert item["requested_by"] == "operator-1"
        assert item["approved_by"] is None
        assert "created_at" in item
        assert "integer" not in json.dumps(body)


def test_list_replay_approvals_public_delivery_id_only(
    partner: Partner,
    failed_delivery: Delivery,
) -> None:
    approval = ReplayApproval(
        id=generate_uuidv7(),
        delivery_id=failed_delivery.id,
        reason="check public id",
        requested_by="operator-2",
        status=ReplayApprovalStatus.PENDING.value,
        created_at=datetime.now(UTC),
    )
    session = ListApprovalsSession([(approval, failed_delivery, partner)])
    with _build_app(session, approval_required=True) as client:
        res = client.get(
            "/admin/v1/replay-approvals?status=pending",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert res.status_code == 200
        item = res.json()["items"][0]
        assert item["delivery_id"] == str(failed_delivery.public_id)
        assert isinstance(item["delivery_id"], str)
        assert uuid.UUID(item["delivery_id"]).version == 7


def test_openapi_replay_approval_no_bigint_ids() -> None:
    spec = create_app().openapi()
    for path_key in (
        "/admin/v1/replay-approvals/{id}/approve",
        "/admin/v1/replay-approvals/{id}/reject",
    ):
        assert path_key in spec["paths"]
        for method in spec["paths"][path_key].values():
            if not isinstance(method, dict):
                continue
            params = method.get("parameters", [])
            for param in params:
                if param.get("in") == "path" and param.get("name") == "id":
                    schema = param.get("schema", {})
                    assert schema.get("format") == "uuid"
                    assert schema.get("type") == "string"
    replay_post = spec["paths"]["/admin/v1/deliveries/{id}/replay"]["post"]
    assert "202" in replay_post["responses"]
    schemas = spec.get("components", {}).get("schemas", {})
    for name, schema in schemas.items():
        if "ReplayApproval" in name:
            for prop_name, prop in schema.get("properties", {}).items():
                if prop_name.endswith("_id") or prop_name == "id":
                    assert prop.get("type") != "integer", f"{name}.{prop_name} must not be integer"

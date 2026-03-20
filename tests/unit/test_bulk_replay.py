"""Unit tests for admin bulk delivery replay."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

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
PARTNER_SLUG_A = "bulk-acme"
PARTNER_SLUG_B = "bulk-beta"
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


class BulkReplaySession:
    def __init__(self, by_public_id: dict[uuid.UUID, tuple[Delivery, Partner]]) -> None:
        self._by_public_id = by_public_id
        self.added: list[object] = []
        self.commit_count = 0

    async def execute(self, _stmt: object) -> FakeResult:
        return FakeResult(row=None)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commit_count += 1

    async def refresh(self, _obj: object) -> None:
        return None


def _role_token(role: str, secret: str = ADMIN_TOKEN) -> str:
    return jwt.encode({"sub": f"user-{role}", "role": role}, secret, algorithm="HS256")


@contextmanager
def _build_app(session: BulkReplaySession) -> Iterator[TestClient]:
    os.environ["ADMIN_BOOTSTRAP_TOKEN"] = ADMIN_TOKEN
    os.environ["KAFKA_BOOTSTRAP_SERVERS"] = "localhost:9092"
    get_settings.cache_clear()
    app = create_app()

    async def override_db() -> AsyncIterator[BulkReplaySession]:
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
def partner_a() -> Partner:
    return Partner(
        id=1,
        public_id=generate_uuidv7(),
        slug=PARTNER_SLUG_A,
        name="ACME",
        status=PartnerStatus.ACTIVE,
        sla_seconds=120,
        rate_limit_rps=100,
        signing_secret_encrypted=None,
    )


@pytest.fixture
def partner_b() -> Partner:
    return Partner(
        id=2,
        public_id=generate_uuidv7(),
        slug=PARTNER_SLUG_B,
        name="Beta",
        status=PartnerStatus.ACTIVE,
        sla_seconds=120,
        rate_limit_rps=100,
        signing_secret_encrypted=None,
    )


@pytest.fixture
def endpoint_a(partner_a: Partner) -> PartnerEndpoint:
    return PartnerEndpoint(
        id=generate_uuidv7(),
        partner_id=partner_a.id,
        direction=EndpointDirection.OUTBOUND,
        url="https://partner-a.example/hooks",
        event_types=[EVENT_TYPE],
        status=EndpointStatus.ACTIVE,
        sla_seconds=90,
        max_attempts=6,
    )


@pytest.fixture
def endpoint_b(partner_b: Partner) -> PartnerEndpoint:
    return PartnerEndpoint(
        id=generate_uuidv7(),
        partner_id=partner_b.id,
        direction=EndpointDirection.OUTBOUND,
        url="https://partner-b.example/hooks",
        event_types=[EVENT_TYPE],
        status=EndpointStatus.ACTIVE,
        sla_seconds=90,
        max_attempts=6,
    )


def _failed_delivery(
    *,
    partner: Partner,
    endpoint: PartnerEndpoint,
    suffix: str,
) -> Delivery:
    return Delivery(
        id=10 + hash(suffix) % 1000,
        public_id=generate_uuidv7(),
        partner_id=partner.id,
        endpoint_id=endpoint.id,
        direction=EndpointDirection.OUTBOUND,
        event_type=EVENT_TYPE,
        idempotency_key=f"idem-{suffix}",
        payload={"order_id": suffix},
        payload_hash=f"hash-{suffix}",
        status=DeliveryStatus.FAILED,
        attempt_count=2,
        max_attempts=6,
        sla_deadline_at=datetime.now(UTC) + timedelta(seconds=90),
        correlation_id=str(generate_uuidv7()),
    )


def _post_bulk_replay(
    client: TestClient,
    *,
    body: dict[str, object],
    token: str | None = ADMIN_TOKEN,
) -> Any:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/admin/v1/deliveries/bulk-replay", json=body, headers=headers)


async def _fetch_side_effect(
    session: BulkReplaySession,
    _session_arg: object,
    delivery_public_id: uuid.UUID,
) -> tuple[Delivery, Partner] | None:
    return session._by_public_id.get(delivery_public_id)


def test_viewer_bulk_replay_returns_403(partner_a: Partner, endpoint_a: PartnerEndpoint) -> None:
    delivery = _failed_delivery(partner=partner_a, endpoint=endpoint_a, suffix="v")
    session = BulkReplaySession({delivery.public_id: (delivery, partner_a)})
    with _build_app(session) as client:
        res = _post_bulk_replay(
            client,
            body={"delivery_ids": [str(delivery.public_id)], "reason": "ops"},
            token=_role_token("hub_viewer"),
        )
        assert res.status_code == 403


def test_operator_bulk_replay_returns_403(partner_a: Partner, endpoint_a: PartnerEndpoint) -> None:
    delivery = _failed_delivery(partner=partner_a, endpoint=endpoint_a, suffix="o")
    session = BulkReplaySession({delivery.public_id: (delivery, partner_a)})
    with _build_app(session) as client:
        res = _post_bulk_replay(
            client,
            body={"delivery_ids": [str(delivery.public_id)], "reason": "ops"},
            token=_role_token("hub_operator"),
        )
        assert res.status_code == 403


@patch("app.domain.services.replay_service.fetch_delivery_with_partner", new_callable=AsyncMock)
def test_admin_bulk_replay_returns_200(
    mock_fetch: AsyncMock,
    partner_a: Partner,
    endpoint_a: PartnerEndpoint,
) -> None:
    delivery = _failed_delivery(partner=partner_a, endpoint=endpoint_a, suffix="a")
    session = BulkReplaySession({delivery.public_id: (delivery, partner_a)})

    async def _fetch(_session: object, pid: uuid.UUID) -> tuple[Delivery, Partner] | None:
        return session._by_public_id.get(pid)

    mock_fetch.side_effect = _fetch

    with (
        _build_app(session) as client,
        patch(
            "app.domain.services.replay_service.is_open",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "app.domain.services.replay_service.allow_request",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        res = _post_bulk_replay(
            client,
            body={"delivery_ids": [str(delivery.public_id)], "reason": "bulk recovery"},
            token=ADMIN_TOKEN,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["replayed"] == [str(delivery.public_id)]
        assert body["requested"] == [str(delivery.public_id)]


def test_empty_reason_returns_422(partner_a: Partner, endpoint_a: PartnerEndpoint) -> None:
    delivery = _failed_delivery(partner=partner_a, endpoint=endpoint_a, suffix="r")
    session = BulkReplaySession({delivery.public_id: (delivery, partner_a)})
    with _build_app(session) as client:
        for bad in ("", "   "):
            res = _post_bulk_replay(
                client,
                body={"delivery_ids": [str(delivery.public_id)], "reason": bad},
            )
            assert res.status_code == 422, res.text


def test_empty_ids_returns_422() -> None:
    session = BulkReplaySession({})
    with _build_app(session) as client:
        res = _post_bulk_replay(client, body={"delivery_ids": [], "reason": "ops"})
        assert res.status_code == 422


def test_too_many_ids_returns_422() -> None:
    session = BulkReplaySession({})
    ids = [str(generate_uuidv7()) for _ in range(101)]
    with _build_app(session) as client:
        res = _post_bulk_replay(client, body={"delivery_ids": ids, "reason": "ops"})
        assert res.status_code == 422


@patch("app.domain.services.replay_service.fetch_delivery_with_partner", new_callable=AsyncMock)
def test_open_circuit_skips_partner_others_replayed(
    mock_fetch: AsyncMock,
    partner_a: Partner,
    partner_b: Partner,
    endpoint_a: PartnerEndpoint,
    endpoint_b: PartnerEndpoint,
) -> None:
    delivery_a = _failed_delivery(partner=partner_a, endpoint=endpoint_a, suffix="cb-a")
    delivery_b = _failed_delivery(partner=partner_b, endpoint=endpoint_b, suffix="cb-b")
    session = BulkReplaySession(
        {
            delivery_a.public_id: (delivery_a, partner_a),
            delivery_b.public_id: (delivery_b, partner_b),
        }
    )

    async def _fetch(_session: object, pid: uuid.UUID) -> tuple[Delivery, Partner] | None:
        return session._by_public_id.get(pid)

    mock_fetch.side_effect = _fetch

    async def _is_open(_redis: object, *, partner_slug: str, settings: object) -> bool:
        return partner_slug == PARTNER_SLUG_A

    with (
        _build_app(session) as client,
        patch("app.domain.services.replay_service.is_open", side_effect=_is_open),
        patch(
            "app.domain.services.replay_service.allow_request",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        res = _post_bulk_replay(
            client,
            body={
                "delivery_ids": [str(delivery_a.public_id), str(delivery_b.public_id)],
                "reason": "partial recovery",
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["skipped_open_circuit"] == [str(delivery_a.public_id)]
        assert body["replayed"] == [str(delivery_b.public_id)]
        outbox_rows = [obj for obj in session.added if isinstance(obj, OutboxEvent)]
        assert len(outbox_rows) == 1


@patch("app.domain.services.replay_service.fetch_delivery_with_partner", new_callable=AsyncMock)
def test_rate_limit_deny_skips_delivery(
    mock_fetch: AsyncMock,
    partner_a: Partner,
    endpoint_a: PartnerEndpoint,
) -> None:
    delivery = _failed_delivery(partner=partner_a, endpoint=endpoint_a, suffix="rl")
    session = BulkReplaySession({delivery.public_id: (delivery, partner_a)})

    async def _fetch(_session: object, pid: uuid.UUID) -> tuple[Delivery, Partner] | None:
        return session._by_public_id.get(pid)

    mock_fetch.side_effect = _fetch

    with (
        _build_app(session) as client,
        patch(
            "app.domain.services.replay_service.is_open",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "app.domain.services.replay_service.allow_request",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        res = _post_bulk_replay(
            client,
            body={"delivery_ids": [str(delivery.public_id)], "reason": "rate limited"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["skipped_rate_limited"] == [str(delivery.public_id)]
        assert body["replayed"] == []
        outbox_rows = [obj for obj in session.added if isinstance(obj, OutboxEvent)]
        assert len(outbox_rows) == 0


@patch("app.domain.services.replay_service.fetch_delivery_with_partner", new_callable=AsyncMock)
def test_unknown_id_not_found_non_failed_skipped(
    mock_fetch: AsyncMock,
    partner_a: Partner,
    endpoint_a: PartnerEndpoint,
) -> None:
    failed = _failed_delivery(partner=partner_a, endpoint=endpoint_a, suffix="f")
    pending = Delivery(
        id=99,
        public_id=generate_uuidv7(),
        partner_id=partner_a.id,
        endpoint_id=endpoint_a.id,
        direction=EndpointDirection.OUTBOUND,
        event_type=EVENT_TYPE,
        idempotency_key="idem-pending",
        payload={"order_id": "p"},
        payload_hash="hp",
        status=DeliveryStatus.PENDING,
        attempt_count=0,
        max_attempts=6,
        sla_deadline_at=datetime.now(UTC) + timedelta(seconds=90),
        correlation_id=str(generate_uuidv7()),
    )
    unknown_id = generate_uuidv7()
    session = BulkReplaySession(
        {
            failed.public_id: (failed, partner_a),
            pending.public_id: (pending, partner_a),
        }
    )

    async def _fetch(_session: object, pid: uuid.UUID) -> tuple[Delivery, Partner] | None:
        return session._by_public_id.get(pid)

    mock_fetch.side_effect = _fetch

    with (
        _build_app(session) as client,
        patch(
            "app.domain.services.replay_service.is_open",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "app.domain.services.replay_service.allow_request",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        res = _post_bulk_replay(
            client,
            body={
                "delivery_ids": [str(unknown_id), str(pending.public_id), str(failed.public_id)],
                "reason": "mixed batch",
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["not_found"] == [str(unknown_id)]
        assert body["skipped_invalid_status"] == [str(pending.public_id)]
        assert body["replayed"] == [str(failed.public_id)]


def test_openapi_bulk_replay_path_and_no_bigint_ids() -> None:
    spec = create_app().openapi()
    post = spec["paths"]["/admin/v1/deliveries/bulk-replay"]["post"]
    assert "admin" in post["tags"]
    for code in ("401", "403", "422"):
        assert code in post["responses"]
    response_schema = post["responses"]["200"]["content"]["application/json"]["schema"]
    if "$ref" in response_schema:
        ref_name = response_schema["$ref"].rsplit("/", 1)[-1]
        response_schema = spec["components"]["schemas"][ref_name]
    props = response_schema["properties"]
    for field in (
        "requested",
        "replayed",
        "skipped_open_circuit",
        "skipped_rate_limited",
        "skipped_invalid_status",
        "not_found",
    ):
        assert field in props
    delivery_schema = spec["components"]["schemas"]["DeliveryResponse"]
    assert delivery_schema["properties"]["id"]["type"] == "string"
    assert delivery_schema["properties"]["id"].get("format") == "uuid"
    partner_schema = spec["components"]["schemas"].get("PartnerResponse")
    if partner_schema is not None:
        assert partner_schema["properties"]["id"]["type"] == "string"

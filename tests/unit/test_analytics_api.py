"""Unit tests for admin analytics summary and overview endpoints."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.api.deps import get_db, get_now, get_redis
from app.config import get_settings
from app.domain.enums import (
    DeliveryStatus,
    EndpointDirection,
    EndpointStatus,
    PartnerStatus,
)
from app.domain.ids import generate_uuidv7
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.main import create_app

ADMIN_TOKEN = "test-admin-bootstrap-token-at-least-32-bytes"
PARTNER_SLUG = "analytics-acme"
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


class SummarySession:
    def __init__(
        self,
        *,
        partner: Partner | None,
        terminal_rows: list[tuple[str, bool]],
        durations: list[int | None],
        oldest_dlq_created_at: datetime | None,
    ) -> None:
        self._partner = partner
        self._terminal_rows = terminal_rows
        self._durations = durations
        self._oldest_dlq_created_at = oldest_dlq_created_at
        self._call = 0

    async def execute(self, _stmt: object) -> FakeResult:
        self._call += 1
        if self._call == 1:
            return FakeResult(scalar=self._partner)
        if self._call == 2:
            return FakeResult(rows=self._terminal_rows)
        if self._call == 3:
            return FakeResult(rows=[(d,) for d in self._durations if d is not None])
        if self._call == 4:
            return FakeResult(scalar=self._oldest_dlq_created_at)
        raise AssertionError(f"unexpected execute call {self._call}")


class OverviewSession:
    def __init__(
        self,
        *,
        dlq_count: int,
        partner_rows: list[tuple[uuid.UUID, str, str, bool]],
    ) -> None:
        self._dlq_count = dlq_count
        self._partner_rows = partner_rows
        self._call = 0

    async def execute(self, _stmt: object) -> FakeResult:
        self._call += 1
        if self._call == 1:
            return FakeResult(scalar=self._dlq_count)
        if self._call == 2:
            return FakeResult(rows=self._partner_rows)
        raise AssertionError(f"unexpected execute call {self._call}")


class NotFoundSession:
    async def execute(self, _stmt: object) -> FakeResult:
        return FakeResult(scalar=None)


@contextmanager
def _build_app(
    session: object,
    *,
    redis: object | None = None,
    now_ts: int | None = None,
) -> Iterator[TestClient]:
    os.environ["ADMIN_BOOTSTRAP_TOKEN"] = ADMIN_TOKEN
    get_settings.cache_clear()
    app = create_app()

    async def override_db() -> AsyncIterator[object]:
        yield session

    app.dependency_overrides[get_db] = override_db

    def override_redis() -> object | None:
        return redis

    app.dependency_overrides[get_redis] = override_redis

    if now_ts is not None:

        async def override_now() -> int:
            return now_ts

        app.dependency_overrides[get_now] = override_now

    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()
    app.dependency_overrides.clear()


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


def _role_token(role: str, secret: str = ADMIN_TOKEN) -> str:
    return jwt.encode({"sub": f"user-{role}", "role": role}, secret, algorithm="HS256")


def _auth(token: str | None = ADMIN_TOKEN) -> dict[str, str]:
    if token is None:
        return {}
    return {"Authorization": f"Bearer {token}"}


def test_viewer_can_get_partner_summary(partner: Partner) -> None:
    session = SummarySession(
        partner=partner,
        terminal_rows=[],
        durations=[],
        oldest_dlq_created_at=None,
    )
    with _build_app(session, redis=None) as client:
        res = client.get(
            f"/admin/v1/analytics/partners/{partner.public_id}/summary",
            headers=_auth(_role_token("hub_viewer")),
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["id"] == str(partner.public_id)
        assert body["slug"] == partner.slug
        assert body["window_hours"] == 24


def test_missing_auth_returns_401(partner: Partner) -> None:
    session = SummarySession(
        partner=partner,
        terminal_rows=[],
        durations=[],
        oldest_dlq_created_at=None,
    )
    with _build_app(session) as client:
        res = client.get(
            f"/admin/v1/analytics/partners/{partner.public_id}/summary",
            headers=_auth(None),
        )
        assert res.status_code == 401


def test_unknown_partner_returns_404() -> None:
    with _build_app(NotFoundSession()) as client:
        missing = generate_uuidv7()
        res = client.get(
            f"/admin/v1/analytics/partners/{missing}/summary",
            headers=_auth(),
        )
        assert res.status_code == 404


def test_partner_summary_metrics(partner: Partner) -> None:
    terminal_rows = [
        (DeliveryStatus.DELIVERED.value, False),
        (DeliveryStatus.DELIVERED.value, True),
        (DeliveryStatus.FAILED.value, False),
    ]
    session = SummarySession(
        partner=partner,
        terminal_rows=terminal_rows,
        durations=[10, 20, 100],
        oldest_dlq_created_at=None,
    )
    with _build_app(session, redis=None) as client:
        res = client.get(
            f"/admin/v1/analytics/partners/{partner.public_id}/summary",
            headers=_auth(),
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["success_rate"] == pytest.approx(2 / 3)
        assert body["sla_compliance_pct"] == pytest.approx(100 * 2 / 3)
        assert body["sla_breaches"] == 1
        assert body["p95_latency_ms"] == 100


def test_circuit_state_unknown_when_redis_none(partner: Partner) -> None:
    session = SummarySession(
        partner=partner,
        terminal_rows=[],
        durations=[],
        oldest_dlq_created_at=None,
    )
    with _build_app(session, redis=None) as client:
        res = client.get(
            f"/admin/v1/analytics/partners/{partner.public_id}/summary",
            headers=_auth(),
        )
        assert res.status_code == 200
        assert res.json()["circuit_state"] == "unknown"


@patch("app.domain.services.analytics_service.get_analytics_circuit_state", new_callable=AsyncMock)
def test_circuit_state_open_when_redis_reports_open(
    mock_cb: AsyncMock,
    partner: Partner,
) -> None:
    from app.domain.services.circuit_breaker import AnalyticsCircuitState

    mock_cb.return_value = AnalyticsCircuitState.OPEN
    session = SummarySession(
        partner=partner,
        terminal_rows=[],
        durations=[],
        oldest_dlq_created_at=None,
    )
    fake_redis = object()
    with _build_app(session, redis=fake_redis) as client:
        res = client.get(
            f"/admin/v1/analytics/partners/{partner.public_id}/summary",
            headers=_auth(),
        )
        assert res.status_code == 200
        assert res.json()["circuit_state"] == "open"
        mock_cb.assert_awaited_once()


def test_dlq_age_seconds_oldest_unacked(partner: Partner) -> None:
    now = datetime.now(UTC)
    now_ts = int(now.timestamp())
    oldest = now - timedelta(hours=2)
    session = SummarySession(
        partner=partner,
        terminal_rows=[],
        durations=[],
        oldest_dlq_created_at=oldest,
    )
    with _build_app(session, redis=None, now_ts=now_ts) as client:
        res = client.get(
            f"/admin/v1/analytics/partners/{partner.public_id}/summary",
            headers=_auth(),
        )
        assert res.status_code == 200
        age = res.json()["dlq_age_seconds"]
        assert age == pytest.approx(int((now - oldest).total_seconds()), abs=2)


def test_dlq_age_zero_when_no_unacked(partner: Partner) -> None:
    session = SummarySession(
        partner=partner,
        terminal_rows=[],
        durations=[],
        oldest_dlq_created_at=None,
    )
    with _build_app(session, redis=None) as client:
        res = client.get(
            f"/admin/v1/analytics/partners/{partner.public_id}/summary",
            headers=_auth(),
        )
        assert res.status_code == 200
        assert res.json()["dlq_age_seconds"] == 0


def test_overview_top_failing_partners_sorted() -> None:
    partner_a_id = generate_uuidv7()
    partner_b_id = generate_uuidv7()
    partner_rows = [
        (partner_a_id, "alpha", DeliveryStatus.DELIVERED.value, False),
        (partner_a_id, "alpha", DeliveryStatus.FAILED.value, False),
        (partner_a_id, "alpha", DeliveryStatus.FAILED.value, True),
        (partner_b_id, "beta", DeliveryStatus.DELIVERED.value, False),
        (partner_b_id, "beta", DeliveryStatus.DELIVERED.value, False),
        (partner_b_id, "beta", DeliveryStatus.DELIVERED.value, False),
    ]
    session = OverviewSession(dlq_count=3, partner_rows=partner_rows)
    with _build_app(session) as client:
        res = client.get("/admin/v1/analytics/overview", headers=_auth())
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["window_hours"] == 24
        assert body["dlq_count"] == 3
        top = body["top_failing_partners"]
        assert len(top) == 2
        assert top[0]["id"] == str(partner_a_id)
        assert top[0]["slug"] == "alpha"
        assert top[0]["success_rate"] == pytest.approx(1 / 3)
        assert top[0]["sla_breaches"] == 1
        assert top[1]["id"] == str(partner_b_id)
        assert isinstance(top[0]["id"], str)
        assert not str(top[0]["id"]).isdigit()
        assert uuid.UUID(top[0]["id"]).version == 7


def test_overview_avg_sla_compliance() -> None:
    partner_a_id = generate_uuidv7()
    partner_b_id = generate_uuidv7()
    partner_rows = [
        (partner_a_id, "alpha", DeliveryStatus.DELIVERED.value, False),
        (partner_a_id, "alpha", DeliveryStatus.FAILED.value, True),
        (partner_b_id, "beta", DeliveryStatus.DELIVERED.value, False),
        (partner_b_id, "beta", DeliveryStatus.DELIVERED.value, False),
    ]
    session = OverviewSession(dlq_count=0, partner_rows=partner_rows)
    with _build_app(session) as client:
        res = client.get("/admin/v1/analytics/overview", headers=_auth())
        assert res.status_code == 200
        avg = res.json()["avg_sla_compliance_pct"]
        expected = (50.0 + 100.0) / 2
        assert avg == pytest.approx(expected)


def test_openapi_analytics_paths_and_schemas() -> None:
    spec = create_app().openapi()
    assert "/admin/v1/analytics/partners/{id}/summary" in spec["paths"]
    assert "/admin/v1/analytics/overview" in spec["paths"]
    summary = spec["paths"]["/admin/v1/analytics/partners/{id}/summary"]["get"]
    overview = spec["paths"]["/admin/v1/analytics/overview"]["get"]
    assert "admin" in summary["tags"]
    assert "admin" in overview["tags"]
    for route in (summary, overview):
        for code in ("401", "403", "404", "422"):
            assert code in route["responses"]
    schemas = spec.get("components", {}).get("schemas", {})
    for key in ("PartnerAnalyticsSummary", "AnalyticsOverview", "TopFailingPartner"):
        assert key in schemas
        props = schemas[key].get("properties", {})
        if "id" in props:
            assert props["id"].get("type") != "integer"
    for key, schema in schemas.items():
        if "Partner" in key or "Delivery" in key or "Analytics" in key:
            props = schema.get("properties", {})
            if "id" in props:
                assert props["id"].get("type") != "integer", f"{key}.id must not be integer"


def test_jwt_secret_at_least_32_bytes() -> None:
    assert len(ADMIN_TOKEN.encode("utf-8")) >= 32


@pytest.mark.asyncio
async def test_analytics_circuit_state_unknown_on_redis_error() -> None:
    from app.domain.services.circuit_breaker import (
        AnalyticsCircuitState,
        get_analytics_circuit_state,
    )

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(side_effect=RedisError("down"))
    settings = get_settings()
    state = await get_analytics_circuit_state(
        mock_redis,
        partner_slug=PARTNER_SLUG,
        settings=settings,
    )
    assert state == AnalyticsCircuitState.UNKNOWN

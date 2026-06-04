"""Unit tests for process-local accept-path TTL cache (Wave 7)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.fixtures.sqlalchemy_stmt import is_select_stmt, stmt_targets_table

from app.domain.enums import EndpointDirection, EndpointStatus, PartnerStatus
from app.domain.ids import generate_uuidv7
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.domain.services.accept_path_cache import (
    _SCHEMA_ABSENT,
    cache_get,
    cache_set,
    invalidate_partner,
    invalidate_partner_endpoints,
    invalidate_schema,
    reset_accept_path_cache,
)
from app.domain.services.outbound_enqueue import enqueue_outbound_for_event


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


class EnqueueCacheFakeSession:
    """Tracks execute count for partner / deliveries / endpoints (schema via table probe)."""

    def __init__(self, partner: Partner, endpoint: PartnerEndpoint) -> None:
        self._partner = partner
        self._endpoint = endpoint
        self._execute_calls = 0
        self.committed = False
        self.added: list[object] = []

    async def execute(self, stmt: object) -> _ExecuteResult:
        if stmt_targets_table(stmt, "payload_schemas"):
            return _ExecuteResult(scalar=None)
        if not is_select_stmt(stmt):
            return _ExecuteResult()
        self._execute_calls += 1
        if self._execute_calls == 1:
            return _ExecuteResult(scalar=self._partner)
        if self._execute_calls == 2:
            return _ExecuteResult(scalars=[self._endpoint])
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


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    reset_accept_path_cache()


def test_cache_set_and_get() -> None:
    cache_set("partner:abc", "value")
    assert cache_get("partner:abc") == "value"


def test_cache_expiry_uses_monotonic(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_accept_path_cache()
    times = [100.0, 100.0, 131.0]
    monkeypatch.setattr(
        "app.domain.services.accept_path_cache.time.monotonic",
        lambda: times.pop(0),
    )
    cache_set("schema:order.created", "row", ttl=30.0)
    assert cache_get("schema:order.created") == "row"
    assert cache_get("schema:order.created") is None


def test_invalidate_partner_drops_partner_key() -> None:
    reset_accept_path_cache()
    public_id = generate_uuidv7()
    partner = Partner(
        id=7,
        public_id=public_id,
        slug="p",
        name="P",
        status=PartnerStatus.ACTIVE,
        sla_seconds=60,
        rate_limit_rps=100,
        signing_secret_encrypted=None,
    )
    cache_set(f"partner:{public_id}", partner)
    invalidate_partner(public_id)
    assert cache_get(f"partner:{public_id}") is None


def test_invalidate_partner_drops_endpoint_keys_for_cached_partner() -> None:
    reset_accept_path_cache()
    public_id = generate_uuidv7()
    partner = Partner(
        id=42,
        public_id=public_id,
        slug="p",
        name="P",
        status=PartnerStatus.ACTIVE,
        sla_seconds=60,
        rate_limit_rps=100,
        signing_secret_encrypted=None,
    )
    cache_set(f"partner:{public_id}", partner)
    cache_set("endpoints:42:order.created", [])
    invalidate_partner(public_id)
    assert cache_get("endpoints:42:order.created") is None


def test_invalidate_partner_endpoints_drops_all_event_types() -> None:
    reset_accept_path_cache()
    cache_set("endpoints:5:order.created", [])
    cache_set("endpoints:5:order.updated", [])
    invalidate_partner_endpoints(5)
    assert cache_get("endpoints:5:order.created") is None
    assert cache_get("endpoints:5:order.updated") is None


def test_invalidate_schema() -> None:
    reset_accept_path_cache()
    cache_set("schema:order.created", _SCHEMA_ABSENT)
    invalidate_schema("order.created")
    assert cache_get("schema:order.created") is None


def test_reset_accept_path_cache() -> None:
    cache_set("partner:x", "y")
    reset_accept_path_cache()
    assert cache_get("partner:x") is None


@pytest.mark.asyncio
async def test_enqueue_second_call_skips_partner_schema_endpoints_selects() -> None:
    reset_accept_path_cache()
    partner = Partner(
        id=1,
        public_id=generate_uuidv7(),
        slug="acme",
        name="ACME",
        status=PartnerStatus.ACTIVE,
        sla_seconds=120,
        rate_limit_rps=100,
        signing_secret_encrypted=None,
    )
    endpoint = PartnerEndpoint(
        id=generate_uuidv7(),
        partner_id=partner.id,
        direction=EndpointDirection.OUTBOUND,
        url="https://example.com/hook",
        event_types=["order.created"],
        status=EndpointStatus.ACTIVE,
        sla_seconds=90,
        max_attempts=6,
    )
    session = EnqueueCacheFakeSession(partner, endpoint)
    now = datetime.fromtimestamp(1_720_000_000, tz=UTC)
    kwargs = {
        "partner_id": partner.public_id,
        "event_type": "order.created",
        "payload": {"order_id": "ord_1"},
        "correlation_id": str(generate_uuidv7()),
        "now": now,
    }
    await enqueue_outbound_for_event(session, idempotency_key="idem-1", **kwargs)
    first_calls = session._execute_calls
    assert first_calls == 2

    session2 = EnqueueCacheFakeSession(partner, endpoint)
    await enqueue_outbound_for_event(session2, idempotency_key="idem-2", **kwargs)
    assert session2._execute_calls == 0

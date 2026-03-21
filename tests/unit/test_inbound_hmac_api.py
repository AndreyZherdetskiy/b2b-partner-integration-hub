"""Unit tests for inbound HMAC, API key, rate limit, and idempotency (spec §7.1.1)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.selectable import Select
from tests.fixtures.sqlalchemy_stmt import stmt_targets_table

from app.api.deps import get_db, get_now
from app.config import get_settings
from app.domain.enums import PartnerStatus
from app.domain.ids import generate_uuidv7
from app.domain.models.api_key import PartnerApiKey
from app.domain.models.inbound_event import InboundEvent
from app.domain.models.outbox import OutboxEvent
from app.domain.models.partner import Partner
from app.domain.services.api_keys import generate_api_key
from app.domain.services.hmac_service import sign
from app.domain.services.idempotency import idempotency_redis_key
from app.domain.services.secrets import encrypt_signing_secret
from app.main import create_app

FIXED_NOW = 1_720_000_000
SIGNING_SECRET = "whsec_unit_test_secret"
PARTNER_SLUG = "acme-inbound"


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _ScalarsResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def scalars(self) -> _ScalarsResult:
        return self

    def all(self) -> list[object]:
        return self._values

    def scalar_one_or_none(self) -> object | None:
        return self._values[0] if self._values else None


def _targets_signing_secrets(stmt: object) -> bool:
    if not isinstance(stmt, Select):
        return False
    for entity in stmt._raw_columns:
        table = getattr(getattr(entity, "entity_namespace", None), "__table__", None)
        if table is not None and table.name == "partner_signing_secrets":
            return True
    return False


class FakeSession:
    def __init__(self, partner: Partner | None, api_keys: list[PartnerApiKey]) -> None:
        self._partner = partner
        self._api_keys = api_keys
        self._execute_calls = 0
        self.committed = False
        self.added: list[object] = []

    async def execute(self, stmt: object) -> _ScalarResult | _ScalarsResult:
        if _targets_signing_secrets(stmt):
            return _ScalarsResult([])
        if stmt_targets_table(stmt, "payload_schemas"):
            return _ScalarResult(None)
        self._execute_calls += 1
        if self._execute_calls == 1:
            return _ScalarResult(self._partner)
        return _ScalarsResult(self._api_keys)

    def add(self, obj: object) -> None:
        if isinstance(obj, MagicMock):
            return
        if isinstance(obj, InboundEvent) and obj.id is None:
            obj.id = generate_uuidv7()
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        return None


class FakeRedis:
    def __init__(self, *, reject: bool = False) -> None:
        self.store: dict[str, bytes] = {}
        self.reject = reject

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(
        self, key: str, value: str | bytes, ex: int | None = None, nx: bool = False
    ) -> bool:
        raw = value.encode() if isinstance(value, str) else value
        if nx and key in self.store:
            return False
        self.store[key] = raw
        return True

    async def incr(self, key: str) -> int:
        current = int(self.store.get(key, b"0").decode())
        current += 1
        self.store[key] = str(current).encode()
        return current

    async def expire(self, key: str, ttl: int) -> None:
        return None


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


class IntegrityRetrySession(FakeSession):
    def __init__(
        self,
        partner: Partner | None,
        api_keys: list[PartnerApiKey],
        *,
        existing_inbound: InboundEvent,
    ) -> None:
        super().__init__(partner, api_keys)
        self._existing_inbound = existing_inbound
        self._flush_attempted = False

    async def flush(self) -> None:
        if not self._flush_attempted:
            self._flush_attempted = True
            raise IntegrityError("duplicate", {}, Exception())

    async def execute(self, stmt: object) -> _ScalarResult | _ScalarsResult:
        if _targets_signing_secrets(stmt):
            return _ScalarsResult([])
        if stmt_targets_table(stmt, "payload_schemas"):
            return _ScalarResult(None)
        self._execute_calls += 1
        if self._execute_calls == 1:
            return _ScalarResult(self._partner)
        if self._execute_calls == 2:
            return _ScalarsResult(self._api_keys)
        return _ScalarResult(self._existing_inbound)


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode("ascii")


@pytest.fixture
def api_key_material() -> tuple[str, str, str]:
    return generate_api_key()


@pytest.fixture
def partner(fernet_key: str) -> Partner:
    encrypted = encrypt_signing_secret(SIGNING_SECRET.encode(), fernet_key)
    return Partner(
        id=1,
        public_id=generate_uuidv7(),
        slug=PARTNER_SLUG,
        name="ACME",
        status=PartnerStatus.ACTIVE,
        sla_seconds=60,
        rate_limit_rps=100,
        signing_secret_encrypted=encrypted,
    )


@pytest.fixture
def api_key_row(partner: Partner, api_key_material: tuple[str, str, str]) -> PartnerApiKey:
    _full, prefix, key_hash = api_key_material
    return PartnerApiKey(
        id=generate_uuidv7(),
        partner_id=partner.id,
        key_prefix=prefix,
        key_hash=key_hash,
        scopes=["inbound:write"],
        expires_at=None,
        revoked_at=None,
        created_at=datetime.now(UTC),
    )


@contextmanager
def _build_app(
    fernet_key: str,
    session: FakeSession,
    *,
    redis: FakeRedis | None = None,
    producer: FakeKafkaProducer | None = None,
    now: int = FIXED_NOW,
) -> Iterator[TestClient]:
    os.environ["FERNET_KEY"] = fernet_key
    os.environ["REDIS_URL"] = "redis://localhost:6379/0"
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
        client.app.state.redis = redis if redis is not None else FakeRedis()
        client.app.state.kafka_producer = producer if producer is not None else FakeKafkaProducer()
        yield client
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _noop_kafka_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(self: object) -> None:
        return None

    monkeypatch.setattr("aiokafka.AIOKafkaProducer.start", _noop)
    monkeypatch.setattr("aiokafka.AIOKafkaProducer.stop", _noop)


def _post_signed(
    client: TestClient,
    *,
    body: bytes,
    api_key: str,
    timestamp: str | None = None,
    signature: str | None = None,
    idempotency_key: str = "idem-1",
) -> Any:
    ts = timestamp or str(FIXED_NOW)
    sig = signature if signature is not None else sign(SIGNING_SECRET, ts, body)
    return client.post(
        f"/inbound/v1/{PARTNER_SLUG}/events",
        content=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "X-Hub-Signature-256": sig,
            "X-Hub-Timestamp": ts,
            "Idempotency-Key": idempotency_key,
            "Content-Type": "application/json",
        },
    )


def test_bad_timestamp_returns_403(
    fernet_key: str,
    partner: Partner,
    api_key_row: PartnerApiKey,
    api_key_material: tuple[str, str, str],
) -> None:
    full_key, _, _ = api_key_material
    session = FakeSession(partner, [api_key_row])
    with _build_app(fernet_key, session, now=FIXED_NOW) as client:
        body = b'{"event_type":"order.created","payload":{"order_id":"1"}}'
        res = _post_signed(client, body=body, api_key=full_key, timestamp="1")
        assert res.status_code == 403


def test_bad_hmac_returns_403(
    fernet_key: str,
    partner: Partner,
    api_key_row: PartnerApiKey,
    api_key_material: tuple[str, str, str],
) -> None:
    full_key, _, _ = api_key_material
    session = FakeSession(partner, [api_key_row])
    with _build_app(fernet_key, session) as client:
        body = b'{"event_type":"order.created","payload":{"order_id":"1"}}'
        res = _post_signed(
            client,
            body=body,
            api_key=full_key,
            signature="sha256=deadbeef",
        )
        assert res.status_code == 403


def test_missing_api_key_returns_401(
    fernet_key: str,
    partner: Partner,
    api_key_row: PartnerApiKey,
) -> None:
    session = FakeSession(partner, [api_key_row])
    with _build_app(fernet_key, session) as client:
        body = b'{"event_type":"order.created","payload":{"order_id":"1"}}'
        ts = str(FIXED_NOW)
        sig = sign(SIGNING_SECRET, ts, body)
        res = client.post(
            f"/inbound/v1/{PARTNER_SLUG}/events",
            content=body,
            headers={
                "X-Hub-Signature-256": sig,
                "X-Hub-Timestamp": ts,
                "Idempotency-Key": "idem-1",
                "Content-Type": "application/json",
            },
        )
        assert res.status_code == 401


def test_wrong_api_key_returns_401(
    fernet_key: str,
    partner: Partner,
    api_key_row: PartnerApiKey,
    api_key_material: tuple[str, str, str],
) -> None:
    _full, _, _ = api_key_material
    session = FakeSession(partner, [api_key_row])
    with _build_app(fernet_key, session) as client:
        body = b'{"event_type":"order.created","payload":{"order_id":"1"}}'
        res = _post_signed(client, body=body, api_key="pk_live_wrong_key_xxxx")
        assert res.status_code == 401


def test_unknown_event_type_returns_422(
    fernet_key: str,
    partner: Partner,
    api_key_row: PartnerApiKey,
    api_key_material: tuple[str, str, str],
) -> None:
    full_key, _, _ = api_key_material
    session = FakeSession(partner, [api_key_row])
    with _build_app(fernet_key, session) as client:
        body = b'{"event_type":"inventory.sync","payload":{"sku":"x"}}'
        res = _post_signed(client, body=body, api_key=full_key)
        assert res.status_code == 422


def test_accepted_returns_202_and_creates_unpublished_outbox(
    fernet_key: str,
    partner: Partner,
    api_key_row: PartnerApiKey,
    api_key_material: tuple[str, str, str],
) -> None:
    full_key, _, _ = api_key_material
    producer = FakeKafkaProducer()
    session = FakeSession(partner, [api_key_row])
    with _build_app(fernet_key, session, producer=producer) as client:
        body = b'{"event_type":"order.created","payload":{"order_id":"99"}}'
        res = _post_signed(client, body=body, api_key=full_key)
        assert res.status_code == 202, res.text
        payload = res.json()
        assert payload["status"] == "accepted"
        assert uuid.UUID(payload["event_id"]).version == 7
        assert producer.call_count == 0
        outbox_rows = [obj for obj in session.added if isinstance(obj, OutboxEvent)]
        assert len(outbox_rows) == 1
        outbox = outbox_rows[0]
        assert outbox.topic == "hub.inbound.order.created"
        assert outbox.message_key == str(partner.public_id)
        assert outbox.published_at is None
        envelope = outbox.payload
        assert envelope["partner_id"] == str(partner.public_id)
        assert envelope["schema_version"] == 1
        assert uuid.UUID(envelope["correlation_id"]).version == 7


def test_kafka_unavailable_producer_still_returns_202_with_outbox(
    fernet_key: str,
    partner: Partner,
    api_key_row: PartnerApiKey,
    api_key_material: tuple[str, str, str],
) -> None:
    full_key, _, _ = api_key_material
    producer = FakeKafkaProducer(fail=True)
    redis = FakeRedis()
    session = FakeSession(partner, [api_key_row])
    with _build_app(fernet_key, session, redis=redis, producer=producer) as client:
        body = b'{"event_type":"order.created","payload":{"order_id":"fail-1"}}'
        res = _post_signed(client, body=body, api_key=full_key, idempotency_key="idem-kafka-fail")
        assert res.status_code == 202, res.text
        assert session.committed is True
        assert producer.call_count == 0
        outbox_rows = [obj for obj in session.added if isinstance(obj, OutboxEvent)]
        assert len(outbox_rows) == 1
        cache_key = idempotency_redis_key(partner.id, "idem-kafka-fail")
        assert cache_key in redis.store


def test_integrity_error_existing_inbound_returns_200_without_second_outbox(
    fernet_key: str,
    partner: Partner,
    api_key_row: PartnerApiKey,
    api_key_material: tuple[str, str, str],
) -> None:
    full_key, _, _ = api_key_material
    existing = InboundEvent(
        id=generate_uuidv7(),
        partner_id=partner.id,
        idempotency_key="idem-retry",
        event_type="order.created",
        payload={"order_id": "retry-1"},
        payload_hash="abc",
        signature_valid=True,
        received_at=datetime.now(UTC),
        published_at=None,
        correlation_id=str(generate_uuidv7()),
    )
    producer = FakeKafkaProducer()
    session = IntegrityRetrySession(partner, [api_key_row], existing_inbound=existing)
    with _build_app(fernet_key, session, producer=producer) as client:
        body = b'{"event_type":"order.created","payload":{"order_id":"retry-1"}}'
        res = _post_signed(client, body=body, api_key=full_key, idempotency_key="idem-retry")
        assert res.status_code == 200, res.text
        assert res.json()["event_id"] == str(existing.id)
        assert res.json()["status"] == "duplicate"
        assert producer.call_count == 0
        outbox_rows = [obj for obj in session.added if isinstance(obj, OutboxEvent)]
        assert outbox_rows == []


def test_rate_limit_returns_429(
    fernet_key: str,
    partner: Partner,
    api_key_row: PartnerApiKey,
    api_key_material: tuple[str, str, str],
) -> None:
    full_key, _, _ = api_key_material
    partner.rate_limit_rps = 0
    session = FakeSession(partner, [api_key_row])
    with _build_app(fernet_key, session) as client:
        body = b'{"event_type":"order.created","payload":{"order_id":"1"}}'
        res = _post_signed(client, body=body, api_key=full_key)
        assert res.status_code == 429


def test_openapi_inbound_responses_and_examples() -> None:
    spec = create_app().openapi()
    post = spec["paths"]["/inbound/v1/{partner_slug}/events"]["post"]
    assert "inbound" in post["tags"]
    for code in ("401", "403", "409", "413", "422", "429"):
        assert code in post["responses"]
    request_body = post["requestBody"]["content"]["application/json"]
    assert "happy" in request_body["examples"]
    assert "bad_timestamp" in request_body["examples"]

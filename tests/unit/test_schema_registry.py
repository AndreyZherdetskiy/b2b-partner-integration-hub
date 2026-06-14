"""Unit tests for JSON Schema registry stub (Stage 3 Task 1)."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from jsonschema.validators import Draft202012Validator
from sqlalchemy.sql.selectable import Select

import app.domain.services.schema_registry as schema_registry
from app.api.deps import get_db, get_now
from app.config import get_settings
from app.domain.enums import (
    EndpointDirection,
    EndpointStatus,
    PartnerStatus,
    PayloadSchemaStatus,
)
from app.domain.ids import generate_uuidv7
from app.domain.models.api_key import PartnerApiKey
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.inbound_event import InboundEvent
from app.domain.models.partner import Partner
from app.domain.models.payload_schema import PayloadSchema
from app.domain.services.api_keys import generate_api_key
from app.domain.services.hmac_service import sign
from app.domain.services.schema_registry import SchemaValidationError, validate_payload
from app.domain.services.secrets import encrypt_signing_secret
from app.main import create_app

FIXED_NOW = 1_720_000_000
ADMIN_TOKEN = "test-admin-bootstrap-token-at-least-32-bytes"
SIGNING_SECRET = "whsec_schema_test_secret"
PARTNER_SLUG = "acme-schema"
EVENT_TYPE = "order.created"

ORDER_CREATED_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
    "additionalProperties": True,
}


def _targets_payload_schemas(stmt: object) -> bool:
    if not isinstance(stmt, Select):
        return False
    for entity in stmt._raw_columns:
        table = getattr(getattr(entity, "entity_namespace", None), "__table__", None)
        if table is not None and table.name == "payload_schemas":
            return True
    return False


def _active_order_created_schema() -> PayloadSchema:
    return PayloadSchema(
        id=generate_uuidv7(),
        event_type=EVENT_TYPE,
        version=1,
        json_schema=ORDER_CREATED_SCHEMA,
        status=PayloadSchemaStatus.ACTIVE,
    )


def test_validate_payload_reuses_compiled_validator_for_same_schema_id_version() -> None:
    schema_registry._VALIDATORS.clear()
    row = _active_order_created_schema()
    with patch(
        "app.domain.services.schema_registry.Draft202012Validator",
        wraps=Draft202012Validator,
    ) as ctor:
        validate_payload(EVENT_TYPE, {"order_id": "a"}, row)
        validate_payload(EVENT_TYPE, {"order_id": "b"}, row)
        assert ctor.call_count == 1


def test_validate_no_schema_is_noop() -> None:
    validate_payload(EVENT_TYPE, {"order_id": "ord_1"}, None)


def test_validate_valid_order_created_passes() -> None:
    schema_row = _active_order_created_schema()
    validate_payload(EVENT_TYPE, {"order_id": "ord_1"}, schema_row)


def test_validate_missing_order_id_raises() -> None:
    schema_row = _active_order_created_schema()
    with pytest.raises(SchemaValidationError):
        validate_payload(EVENT_TYPE, {"amount": 10}, schema_row)


def test_validate_deprecated_row_is_noop() -> None:
    schema_row = _active_order_created_schema()
    schema_row.status = PayloadSchemaStatus.DEPRECATED
    validate_payload(EVENT_TYPE, {"amount": 10}, schema_row)


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


class OutboundSchemaFakeSession:
    def __init__(
        self,
        partner: Partner,
        endpoint: PartnerEndpoint,
        *,
        schema_row: PayloadSchema | None = None,
    ) -> None:
        self._partner = partner
        self._endpoint = endpoint
        self._schema_row = schema_row
        self._execute_calls = 0
        self.committed = False
        self.added: list[object] = []

    async def execute(self, stmt: object) -> _ScalarResult | _ScalarsResult:
        if _targets_payload_schemas(stmt):
            return _ScalarResult(self._schema_row)
        if not isinstance(stmt, Select):
            return _ScalarsResult([])
        self._execute_calls += 1
        if self._execute_calls == 1:
            return _ScalarResult(self._partner)
        if self._execute_calls == 2:
            return _ScalarsResult([self._endpoint])
        return _ScalarsResult([])

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        return None


class InboundSchemaFakeSession:
    def __init__(
        self,
        partner: Partner,
        api_keys: list[PartnerApiKey],
        *,
        schema_row: PayloadSchema | None = None,
    ) -> None:
        self._partner = partner
        self._api_keys = api_keys
        self._schema_row = schema_row
        self._execute_calls = 0
        self.committed = False
        self.added: list[object] = []

    async def execute(self, stmt: object) -> _ScalarResult | _ScalarsResult:
        if _targets_signing_secrets(stmt):
            return _ScalarsResult([])
        if _targets_payload_schemas(stmt):
            return _ScalarResult(self._schema_row)
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
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

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
        current = int(self.store.get(key, b"0"))
        current += 1
        self.store[key] = str(current).encode()
        return current

    async def expire(self, key: str, seconds: int) -> bool:
        return True


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode("ascii")


@pytest.fixture
def partner(fernet_key: str) -> Partner:
    encrypted = encrypt_signing_secret(SIGNING_SECRET.encode(), fernet_key)
    return Partner(
        id=1,
        public_id=generate_uuidv7(),
        slug=PARTNER_SLUG,
        name="ACME",
        status=PartnerStatus.ACTIVE,
        sla_seconds=120,
        rate_limit_rps=100,
        signing_secret_encrypted=encrypted,
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


@pytest.fixture
def inbound_api_key_material() -> tuple[str, str, str]:
    return generate_api_key()


@pytest.fixture
def inbound_api_key(
    partner: Partner,
    inbound_api_key_material: tuple[str, str, str],
) -> PartnerApiKey:
    _full, prefix, key_hash = inbound_api_key_material
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
def _build_outbound_app(session: OutboundSchemaFakeSession) -> Iterator[TestClient]:
    os.environ["ADMIN_BOOTSTRAP_TOKEN"] = ADMIN_TOKEN
    os.environ["KAFKA_BOOTSTRAP_SERVERS"] = "localhost:9092"
    get_settings.cache_clear()
    app = create_app()

    async def override_db() -> AsyncIterator[OutboundSchemaFakeSession]:
        yield session

    async def override_now() -> int:
        return FIXED_NOW

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_now] = override_now

    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


@contextmanager
def _build_inbound_app(
    fernet_key: str,
    session: InboundSchemaFakeSession,
    redis: FakeRedis,
) -> Iterator[TestClient]:
    os.environ["FERNET_KEY"] = fernet_key
    os.environ["REDIS_URL"] = "redis://localhost:6379/0"
    os.environ["ADMIN_BOOTSTRAP_TOKEN"] = ADMIN_TOKEN
    os.environ["KAFKA_BOOTSTRAP_SERVERS"] = "localhost:9092"
    get_settings.cache_clear()
    app = create_app()

    async def override_db() -> AsyncIterator[InboundSchemaFakeSession]:
        yield session

    async def override_now() -> int:
        return FIXED_NOW

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_now] = override_now

    with TestClient(app) as client:
        client.app.state.redis = redis
        yield client
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _noop_kafka_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(self: object) -> None:
        return None

    monkeypatch.setattr("aiokafka.AIOKafkaProducer.start", _noop)
    monkeypatch.setattr("aiokafka.AIOKafkaProducer.stop", _noop)


def test_outbound_422_when_active_schema_and_invalid_payload(
    partner: Partner,
    endpoint: PartnerEndpoint,
) -> None:
    session = OutboundSchemaFakeSession(
        partner,
        endpoint,
        schema_row=_active_order_created_schema(),
    )
    body = {
        "partner_id": str(partner.public_id),
        "event_type": EVENT_TYPE,
        "payload": {"amount": 10},
        "idempotency_key": "schema-fail-out",
    }
    with _build_outbound_app(session) as client:
        res = client.post(
            "/internal/v1/outbound/events",
            json=body,
            headers={
                "Authorization": f"Bearer {ADMIN_TOKEN}",
                "Content-Type": "application/json",
            },
        )
        assert res.status_code == 422
        assert res.json()["detail"] == "payload does not match registered schema"


def test_inbound_422_when_active_schema_and_invalid_payload(
    fernet_key: str,
    partner: Partner,
    inbound_api_key: PartnerApiKey,
    inbound_api_key_material: tuple[str, str, str],
) -> None:
    raw_key = inbound_api_key_material[0]
    body = {"event_type": EVENT_TYPE, "payload": {"amount": 10}}
    raw_body = json.dumps(body).encode()
    timestamp = str(FIXED_NOW)
    signature = sign(SIGNING_SECRET, timestamp, raw_body)
    session = InboundSchemaFakeSession(
        partner,
        [inbound_api_key],
        schema_row=_active_order_created_schema(),
    )
    redis = FakeRedis()
    with _build_inbound_app(fernet_key, session, redis) as client:
        res = client.post(
            f"/inbound/v1/{PARTNER_SLUG}/events",
            content=raw_body,
            headers={
                "Authorization": f"Bearer {raw_key}",
                "X-Hub-Timestamp": timestamp,
                "X-Hub-Signature-256": signature,
                "Idempotency-Key": "schema-fail-in",
                "Content-Type": "application/json",
            },
        )
        assert res.status_code == 422
        assert res.json()["detail"] == "payload does not match registered schema"


def test_admin_create_schema_returns_201(
    partner: Partner,
) -> None:
    class AdminSchemaSession:
        def __init__(self) -> None:
            self.committed = False
            self.added: list[object] = []

        def add(self, obj: object) -> None:
            if isinstance(obj, PayloadSchema) and obj.id is None:
                obj.id = generate_uuidv7()
            self.added.append(obj)

        async def commit(self) -> None:
            self.committed = True

        async def refresh(self, obj: object) -> None:
            if isinstance(obj, PayloadSchema):
                now = datetime.now(UTC)
                if obj.created_at is None:
                    obj.created_at = now
                if obj.updated_at is None:
                    obj.updated_at = now

    session = AdminSchemaSession()
    os.environ["ADMIN_BOOTSTRAP_TOKEN"] = ADMIN_TOKEN
    get_settings.cache_clear()
    app = create_app()

    async def override_db() -> AsyncIterator[AdminSchemaSession]:
        yield session

    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as client:
        res = client.post(
            "/admin/v1/schemas",
            json={
                "event_type": EVENT_TYPE,
                "version": 1,
                "json_schema": ORDER_CREATED_SCHEMA,
            },
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        assert res.status_code == 201, res.text
        payload = res.json()
        assert uuid.UUID(payload["id"]).version == 7
        assert payload["event_type"] == EVENT_TYPE
        assert payload["version"] == 1
        assert session.committed is True

    get_settings.cache_clear()


def test_openapi_payload_schema_descriptions_and_uuid_id() -> None:
    spec = create_app().openapi()
    create_post = spec["paths"]["/admin/v1/schemas"]["post"]
    assert "admin" in create_post["tags"]
    for code in ("401", "403", "422"):
        assert code in create_post["responses"]
    response_schema = create_post["responses"]["201"]["content"]["application/json"]["schema"]
    ref = response_schema["$ref"].split("/")[-1]
    props = spec["components"]["schemas"][ref]["properties"]
    assert props["id"]["format"] == "uuid"
    for prop_name, prop in props.items():
        assert "description" in prop, f"PayloadSchemaResponse.{prop_name} missing description"

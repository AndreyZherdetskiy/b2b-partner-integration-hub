"""Unit tests for signing secret rotation (spec §6.3, §7.1.2)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import jwt
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.sql.selectable import Select

from app.api.deps import get_db
from app.config import get_settings
from app.domain.enums import (
    DeliveryDirection,
    DeliveryStatus,
    EndpointDirection,
    EndpointStatus,
    PartnerStatus,
    SigningSecretStatus,
)
from app.domain.ids import generate_uuidv7
from app.domain.models.audit import AuditLog
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.domain.models.signing_secret import PartnerSigningSecret
from app.domain.services.hmac_service import sign, verify
from app.domain.services.secrets import encrypt_signing_secret
from app.domain.services.signing_secrets import (
    load_inbound_signing_secrets,
    load_outbound_primary_secret,
    rotate_partner_signing_secret,
)
from app.integrations.http_client import OutboundPostResult, serialize_payload
from app.main import create_app
from app.workers.outbound_processor import ProcessOutcome, process_outbound_message

FIXED_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)
FIXED_NOW_TS = int(FIXED_NOW.timestamp())
PRIMARY_SECRET = b"primary-secret-bytes"
PREVIOUS_SECRET = b"previous-secret-bytes"
FERNET_KEY = Fernet.generate_key().decode("ascii")
PARTNER_PUBLIC_ID = generate_uuidv7()


def _status_filter(stmt: object) -> str | None:
    if not isinstance(stmt, Select):
        return None
    for crit in stmt._where_criteria:
        left = getattr(crit, "left", None)
        right = getattr(crit, "right", None)
        if getattr(left, "key", None) == "status" and hasattr(right, "value"):
            return str(right.value)
    return None


def _targets_signing_secrets(stmt: object) -> bool:
    if not isinstance(stmt, Select):
        return False
    for entity in stmt._raw_columns:
        table = getattr(getattr(entity, "entity_namespace", None), "__table__", None)
        if table is not None and table.name == "partner_signing_secrets":
            return True
    return False


class FakeScalarResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def scalars(self) -> FakeScalarResult:
        return self

    def all(self) -> list[object]:
        return self._values

    def scalar_one_or_none(self) -> object | None:
        return self._values[0] if self._values else None

    def one_or_none(self) -> object | None:
        return self._values[0] if self._values else None


class SigningSecretSession:
    def __init__(self, rows: list[PartnerSigningSecret], partner: Partner | None = None) -> None:
        self._rows = rows
        self._partner = partner
        self.added: list[object] = []
        self.committed = False

    async def execute(self, stmt: object) -> FakeScalarResult:
        if _targets_signing_secrets(stmt):
            status = _status_filter(stmt)
            if status == SigningSecretStatus.PRIMARY.value:
                primary = [r for r in self._rows if r.status == SigningSecretStatus.PRIMARY.value]
                return FakeScalarResult(primary[:1])
            if status == SigningSecretStatus.PREVIOUS.value:
                previous = [
                    r for r in self._rows if r.status == SigningSecretStatus.PREVIOUS.value
                ]
                return FakeScalarResult(previous)
            return FakeScalarResult(list(self._rows))
        if isinstance(stmt, Select):
            return FakeScalarResult([self._partner] if self._partner else [])
        return FakeScalarResult([])

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, _obj: object) -> None:
        return None


def _partner(*, column_secret: bytes | None = None) -> Partner:
    return Partner(
        id=1,
        public_id=PARTNER_PUBLIC_ID,
        slug="rotate-test",
        name="Rotate Test",
        status=PartnerStatus.ACTIVE,
        sla_seconds=60,
        rate_limit_rps=100,
        auto_replay_enabled=False,
        circuit_breaker_config={},
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        signing_secret_encrypted=column_secret,
    )


def _primary_row(partner_id: int, secret: bytes, *, version: int = 2) -> PartnerSigningSecret:
    return PartnerSigningSecret(
        id=generate_uuidv7(),
        partner_id=partner_id,
        secret_encrypted=encrypt_signing_secret(secret, FERNET_KEY),
        version=version,
        status=SigningSecretStatus.PRIMARY.value,
        valid_from=FIXED_NOW - timedelta(days=1),
        valid_until=None,
        created_at=FIXED_NOW,
    )


def _previous_row(
    partner_id: int,
    secret: bytes,
    *,
    version: int = 1,
    valid_until: datetime | None,
    status: SigningSecretStatus = SigningSecretStatus.PREVIOUS,
) -> PartnerSigningSecret:
    return PartnerSigningSecret(
        id=generate_uuidv7(),
        partner_id=partner_id,
        secret_encrypted=encrypt_signing_secret(secret, FERNET_KEY),
        version=version,
        status=status.value,
        valid_from=FIXED_NOW - timedelta(days=2),
        valid_until=valid_until,
        created_at=FIXED_NOW - timedelta(days=2),
    )


@pytest.mark.asyncio
async def test_previous_hmac_accepted_while_valid_until_future() -> None:
    partner = _partner()
    rows = [
        _primary_row(partner.id, PRIMARY_SECRET, version=2),
        _previous_row(
            partner.id,
            PREVIOUS_SECRET,
            version=1,
            valid_until=FIXED_NOW + timedelta(hours=12),
        ),
    ]
    session = SigningSecretSession(rows, partner)
    primary, previous = await load_inbound_signing_secrets(
        session,
        partner,
        get_settings().model_copy(update={"fernet_key": FERNET_KEY}),
        now=FIXED_NOW,
    )
    assert primary == PRIMARY_SECRET
    assert previous == PREVIOUS_SECRET
    body = b'{"event_type":"order.created","payload":{"order_id":"1"}}'
    ts = str(FIXED_NOW_TS)
    signature = sign(PREVIOUS_SECRET, ts, body)
    assert verify(primary, ts, body, signature, now=FIXED_NOW_TS, previous_secret=previous)


@pytest.mark.asyncio
async def test_previous_hmac_rejected_after_valid_until() -> None:
    partner = _partner()
    rows = [
        _primary_row(partner.id, PRIMARY_SECRET, version=2),
        _previous_row(
            partner.id,
            PREVIOUS_SECRET,
            version=1,
            valid_until=FIXED_NOW - timedelta(seconds=1),
        ),
    ]
    session = SigningSecretSession(rows, partner)
    _primary, previous = await load_inbound_signing_secrets(
        session,
        partner,
        get_settings().model_copy(update={"fernet_key": FERNET_KEY}),
        now=FIXED_NOW,
    )
    assert previous is None


@pytest.mark.asyncio
async def test_previous_hmac_rejected_when_revoked() -> None:
    partner = _partner()
    rows = [
        _primary_row(partner.id, PRIMARY_SECRET, version=2),
        _previous_row(
            partner.id,
            PREVIOUS_SECRET,
            version=1,
            valid_until=FIXED_NOW + timedelta(hours=12),
            status=SigningSecretStatus.REVOKED,
        ),
    ]
    session = SigningSecretSession(rows, partner)
    _primary, previous = await load_inbound_signing_secrets(
        session,
        partner,
        get_settings().model_copy(update={"fernet_key": FERNET_KEY}),
        now=FIXED_NOW,
    )
    assert previous is None


class RotateSession:
    def __init__(self, partner: Partner, rows: list[PartnerSigningSecret]) -> None:
        self.partner = partner
        self.rows = list(rows)
        self.added: list[object] = []
        self.committed = False

    async def execute(self, stmt: object) -> FakeScalarResult:
        if _targets_signing_secrets(stmt):
            status = _status_filter(stmt)
            if status == SigningSecretStatus.PRIMARY.value:
                primary = [r for r in self.rows if r.status == SigningSecretStatus.PRIMARY.value]
                return FakeScalarResult(primary[:1])
            if status == SigningSecretStatus.PREVIOUS.value:
                previous = [r for r in self.rows if r.status == SigningSecretStatus.PREVIOUS.value]
                return FakeScalarResult(previous)
            return FakeScalarResult(list(self.rows))
        if isinstance(stmt, Select):
            return FakeScalarResult([self.partner])
        return FakeScalarResult([])

    def add(self, obj: object) -> None:
        self.added.append(obj)
        if isinstance(obj, PartnerSigningSecret):
            self.rows.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, _obj: object) -> None:
        return None


@pytest.mark.asyncio
async def test_rotate_promotes_primary_to_previous_and_returns_plaintext() -> None:
    partner = _partner(column_secret=encrypt_signing_secret(PRIMARY_SECRET, FERNET_KEY))
    current = _primary_row(partner.id, PRIMARY_SECRET, version=1)
    session = RotateSession(partner, [current])
    settings = get_settings().model_copy(
        update={"fernet_key": FERNET_KEY, "hub_secret_rotation_overlap_hours": 24}
    )

    plaintext = await rotate_partner_signing_secret(
        session,
        partner,
        settings,
        actor_id="admin-1",
        now=FIXED_NOW,
    )

    assert plaintext.startswith("whsec_")
    assert current.status == SigningSecretStatus.PREVIOUS.value
    assert current.valid_until == FIXED_NOW + timedelta(hours=24)
    assert partner.signing_secret_encrypted is not None
    new_rows = [obj for obj in session.added if isinstance(obj, PartnerSigningSecret)]
    assert len(new_rows) == 1
    assert new_rows[0].status == SigningSecretStatus.PRIMARY.value
    assert new_rows[0].version == 2
    audits = [obj for obj in session.added if isinstance(obj, AuditLog)]
    assert len(audits) == 1
    assert audits[0].action == "signing_secret.rotate"
    assert audits[0].resource_id == partner.public_id
    assert uuid.UUID(str(audits[0].resource_id)).version == 7


_JWT_SECRET = "hub-admin-unit-test-secret-32b!!"


def _viewer_token() -> str:
    return jwt.encode({"sub": "viewer-1", "role": "hub_viewer"}, _JWT_SECRET, algorithm="HS256")


def _admin_token() -> str:
    return jwt.encode({"sub": "admin-1", "role": "hub_admin"}, _JWT_SECRET, algorithm="HS256")


@contextmanager
def _admin_app(session: RotateSession) -> Iterator[TestClient]:
    os.environ["FERNET_KEY"] = FERNET_KEY
    os.environ["ADMIN_BOOTSTRAP_TOKEN"] = _JWT_SECRET
    get_settings.cache_clear()
    app = create_app()

    async def override_db() -> AsyncIterator[RotateSession]:
        yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _noop_kafka_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(_self: object) -> None:
        return None

    monkeypatch.setattr("aiokafka.AIOKafkaProducer.start", _noop)
    monkeypatch.setattr("aiokafka.AIOKafkaProducer.stop", _noop)


def test_viewer_rotate_returns_403() -> None:
    partner = _partner(column_secret=encrypt_signing_secret(PRIMARY_SECRET, FERNET_KEY))
    current = _primary_row(partner.id, PRIMARY_SECRET, version=1)
    session = RotateSession(partner, [current])
    with _admin_app(session) as client:
        res = client.post(
            f"/admin/v1/partners/{partner.public_id}/rotate-secret",
            headers={"Authorization": f"Bearer {_viewer_token()}"},
        )
        assert res.status_code == 403


def test_rotate_endpoint_returns_plaintext_once() -> None:
    partner = _partner(column_secret=encrypt_signing_secret(PRIMARY_SECRET, FERNET_KEY))
    current = _primary_row(partner.id, PRIMARY_SECRET, version=1)
    session = RotateSession(partner, [current])
    with _admin_app(session) as client:
        res = client.post(
            f"/admin/v1/partners/{partner.public_id}/rotate-secret",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["signing_secret"].startswith("whsec_")
        assert body["id"] == str(partner.public_id)
        assert current.status == SigningSecretStatus.PREVIOUS.value


def test_openapi_rotate_secret_path_exists() -> None:
    spec = create_app().openapi()
    path = "/admin/v1/partners/{id}/rotate-secret"
    assert path in spec["paths"]
    post = spec["paths"][path]["post"]
    assert "admin" in post["tags"]
    assert post.get("summary")
    assert post.get("description")
    for code in ("401", "403", "404", "422"):
        assert code in post["responses"]
    schemas = spec.get("components", {}).get("schemas", {})
    for key, schema in schemas.items():
        if "Partner" in key or "Delivery" in key:
            props = schema.get("properties", {})
            if "id" in props:
                assert props["id"].get("type") != "integer", f"{key}.id must not be integer"


class OutboundProcessorSession:
    def __init__(
        self,
        row: tuple[Any, Partner, Any] | None,
        secret_rows: list[PartnerSigningSecret],
    ) -> None:
        self._row = row
        self._secret_rows = secret_rows
        self.committed = False
        self.added: list[object] = []

    async def execute(self, stmt: object) -> FakeScalarResult:
        if _targets_signing_secrets(stmt):
            status = _status_filter(stmt)
            if status == SigningSecretStatus.PRIMARY.value:
                primary = [
                    r for r in self._secret_rows if r.status == SigningSecretStatus.PRIMARY.value
                ]
                return FakeScalarResult(primary[:1])
            return FakeScalarResult([])
        if self._row is not None:
            return FakeScalarResult([self._row])
        return FakeScalarResult([None])

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_outbound_uses_primary_secret_from_table() -> None:
    partner = _partner(column_secret=encrypt_signing_secret(b"column-fallback", FERNET_KEY))
    endpoint = PartnerEndpoint(
        id=generate_uuidv7(),
        partner_id=partner.id,
        direction=EndpointDirection.OUTBOUND,
        url="http://partner-mock:8090/hooks",
        event_types=["order.created"],
        status=EndpointStatus.ACTIVE,
        sla_seconds=90,
        max_attempts=3,
        retry_on_status_codes=[],
        timeout_connect_ms=1000,
        timeout_read_ms=2000,
    )
    delivery = Delivery(
        id=42,
        public_id=generate_uuidv7(),
        partner_id=partner.id,
        endpoint_id=endpoint.id,
        direction=DeliveryDirection.OUTBOUND,
        event_type="order.created",
        idempotency_key="idem-1",
        payload={"order_id": "o-1"},
        payload_hash="hash",
        status=DeliveryStatus.PENDING,
        attempt_count=0,
        max_attempts=3,
        sla_deadline_at=FIXED_NOW + timedelta(seconds=120),
        correlation_id=str(generate_uuidv7()),
    )
    primary_row = _primary_row(partner.id, PRIMARY_SECRET, version=3)
    session = OutboundProcessorSession((delivery, partner, endpoint), [primary_row])

    class _Producer:
        async def send_and_wait(self, *args: Any, **kwargs: Any) -> None:
            return None

    captured: dict[str, Any] = {}

    async def _capture_post(**kwargs: Any) -> OutboundPostResult:
        captured.update(kwargs)
        return OutboundPostResult(200, {}, "ok", 5, None)

    settings = get_settings().model_copy(update={"fernet_key": FERNET_KEY})
    envelope = {
        "schema_version": 1,
        "delivery_id": str(delivery.public_id),
        "partner_id": str(partner.public_id),
        "endpoint_id": str(endpoint.id),
        "event_type": "order.created",
        "attempt": 1,
        "payload": delivery.payload,
        "idempotency_key": delivery.idempotency_key,
        "correlation_id": delivery.correlation_id,
        "scheduled_at": FIXED_NOW.isoformat(),
        "sla_deadline_at": delivery.sla_deadline_at.isoformat(),
    }

    with patch("app.workers.outbound_processor.post_outbound", new=_capture_post):
        outcome = await process_outbound_message(
            session,
            _Producer(),
            envelope,
            settings,
            now=FIXED_NOW,
        )

    assert outcome == ProcessOutcome.DELIVERED
    body_bytes = captured["body_bytes"]
    headers = captured["headers"]
    timestamp = headers["X-Hub-Timestamp"]
    signature = headers["X-Hub-Signature-256"]
    assert verify(PRIMARY_SECRET, timestamp, body_bytes, signature, now=int(timestamp))
    assert body_bytes == serialize_payload(delivery.payload)

    loaded = await load_outbound_primary_secret(session, partner, settings)
    assert loaded == PRIMARY_SECRET

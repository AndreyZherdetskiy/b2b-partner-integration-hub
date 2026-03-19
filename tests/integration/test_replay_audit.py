"""Integration test: replay inserts audit_logs with delivery public_id."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from tests.integration.conftest import INTEGRATION_ADMIN_TOKEN, auth_header

from app.domain.enums import DeliveryDirection, DeliveryStatus, EndpointDirection, EndpointStatus
from app.domain.ids import generate_uuidv7
from app.domain.models.audit import AuditLog
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.domain.services.delivery_service import canonical_payload_hash
from app.domain.services.secrets import encrypt_signing_secret

pytestmark = pytest.mark.integration


def _operator_token(secret: str = INTEGRATION_ADMIN_TOKEN) -> str:
    return jwt.encode({"sub": "operator-1", "role": "hub_operator"}, secret, algorithm="HS256")


async def _seed_failed_delivery(
    session: AsyncSession,
    fernet_key: str,
) -> tuple[Partner, Delivery]:
    encrypted = encrypt_signing_secret(b"test-secret", fernet_key)
    partner = Partner(
        slug="replay-audit-partner",
        name="Replay Audit",
        status="active",
        sla_seconds=60,
        rate_limit_rps=100,
        signing_secret_encrypted=encrypted,
    )
    session.add(partner)
    await session.flush()
    endpoint = PartnerEndpoint(
        partner_id=partner.id,
        direction=EndpointDirection.OUTBOUND,
        url="https://partner.example/hooks",
        event_types=["order.created"],
        status=EndpointStatus.ACTIVE,
        sla_seconds=60,
        max_attempts=6,
    )
    session.add(endpoint)
    await session.flush()
    payload = {"order_id": "audit-ord-1"}
    delivery = Delivery(
        partner_id=partner.id,
        endpoint_id=endpoint.id,
        direction=DeliveryDirection.OUTBOUND,
        event_type="order.created",
        idempotency_key="audit-idem-1",
        payload=payload,
        payload_hash=canonical_payload_hash(payload),
        status=DeliveryStatus.FAILED,
        attempt_count=2,
        max_attempts=6,
        sla_deadline_at=datetime.now(UTC) + timedelta(seconds=60),
        correlation_id=str(generate_uuidv7()),
    )
    session.add(delivery)
    await session.commit()
    await session.refresh(partner)
    await session.refresh(delivery)
    return partner, delivery


@pytest.mark.asyncio
async def test_replay_inserts_audit_log_with_public_resource_id(
    client: TestClient,
    db_engine: AsyncEngine,
    fernet_key: str,
) -> None:
    engine = db_engine
    async with AsyncSession(engine, expire_on_commit=False) as session:
        _partner, delivery = await _seed_failed_delivery(session, fernet_key)

    res = client.post(
        f"/admin/v1/deliveries/{delivery.public_id}/replay",
        headers=auth_header(_operator_token()),
        json={"reason": "partner endpoint restored"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "replaying"

    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action == "delivery.replay",
                AuditLog.resource_type == "delivery",
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None
        assert audit.resource_id == delivery.public_id
        assert uuid.UUID(str(audit.resource_id)).version == 7
        assert audit.actor_id == "operator-1"
        assert audit.metadata_.get("reason") == "partner endpoint restored"

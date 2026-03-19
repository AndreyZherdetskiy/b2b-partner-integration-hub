"""Factory helpers for partner/endpoint/delivery rows in integration tests."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import DeliveryDirection, DeliveryStatus, EndpointDirection, EndpointStatus
from app.domain.ids import generate_uuidv7
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.domain.services.delivery_service import canonical_payload_hash
from app.domain.services.secrets import encrypt_signing_secret

PARTNER_MOCK_BASE = os.getenv("PARTNER_MOCK_URL", "http://localhost:8090").rstrip("/")
MOCK_WEBHOOK_URL = f"{PARTNER_MOCK_BASE}/webhook"


async def create_outbound_partner(
    session: AsyncSession,
    *,
    fernet_key: str,
    slug: str,
    mock_scenario: str = "ok",
    max_attempts: int = 8,
    timeout_read_ms: int = 10_000,
    timeout_connect_ms: int = 3_000,
) -> tuple[Partner, PartnerEndpoint, str]:
    encrypted = encrypt_signing_secret(b"integration-test-secret", fernet_key)
    partner = Partner(
        slug=slug,
        name=slug.replace("-", " ").title(),
        status="active",
        sla_seconds=120,
        rate_limit_rps=100,
        signing_secret_encrypted=encrypted,
    )
    session.add(partner)
    await session.flush()
    endpoint = PartnerEndpoint(
        partner_id=partner.id,
        direction=EndpointDirection.OUTBOUND,
        url=MOCK_WEBHOOK_URL,
        event_types=["order.created"],
        status=EndpointStatus.ACTIVE,
        sla_seconds=120,
        max_attempts=max_attempts,
        timeout_connect_ms=timeout_connect_ms,
        timeout_read_ms=timeout_read_ms,
    )
    session.add(endpoint)
    await session.flush()
    await session.refresh(partner)
    await session.refresh(endpoint)
    return partner, endpoint, mock_scenario


async def create_pending_delivery(
    session: AsyncSession,
    *,
    partner: Partner,
    endpoint: PartnerEndpoint,
    idempotency_key: str | None = None,
    payload: dict[str, Any] | None = None,
    status: DeliveryStatus = DeliveryStatus.PENDING,
    attempt_count: int = 0,
) -> Delivery:
    body = payload or {"order_id": f"ord-{generate_uuidv7()}"}
    delivery = Delivery(
        partner_id=partner.id,
        endpoint_id=endpoint.id,
        direction=DeliveryDirection.OUTBOUND,
        event_type="order.created",
        idempotency_key=idempotency_key or f"idem-{generate_uuidv7()}",
        payload=body,
        payload_hash=canonical_payload_hash(body),
        status=status,
        attempt_count=attempt_count,
        max_attempts=endpoint.max_attempts,
        sla_deadline_at=datetime.now(UTC) + timedelta(seconds=partner.sla_seconds),
        correlation_id=str(generate_uuidv7()),
    )
    session.add(delivery)
    await session.commit()
    await session.refresh(delivery)
    return delivery


def outbound_envelope(
    delivery: Delivery,
    partner: Partner,
    endpoint: PartnerEndpoint,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    scheduled = now or datetime.now(UTC)
    return {
        "schema_version": 1,
        "delivery_id": str(delivery.public_id),
        "partner_id": str(partner.public_id),
        "endpoint_id": str(endpoint.id),
        "event_type": delivery.event_type,
        "attempt": delivery.attempt_count + 1,
        "payload": delivery.payload,
        "idempotency_key": delivery.idempotency_key,
        "correlation_id": delivery.correlation_id,
        "scheduled_at": scheduled.isoformat(),
        "sla_deadline_at": delivery.sla_deadline_at.isoformat(),
    }

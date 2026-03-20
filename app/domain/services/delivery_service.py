"""Outbound delivery creation helpers (spec §7.1.5)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import DeliveryDirection, DeliveryStatus, EndpointDirection, EndpointStatus
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner


def derived_idempotency_key(client_key: str, endpoint_public_id: uuid.UUID) -> str:
    return f"{client_key}::{endpoint_public_id}"


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_sla_deadline(
    now: datetime,
    *,
    partner: Partner,
    endpoint: PartnerEndpoint,
) -> datetime:
    seconds = endpoint.sla_seconds if endpoint.sla_seconds is not None else partner.sla_seconds
    return now + timedelta(seconds=seconds)


async def fetch_partner_by_public_id(
    session: AsyncSession,
    partner_public_id: object,
) -> Partner | None:
    result = await session.execute(select(Partner).where(Partner.public_id == partner_public_id))
    return result.scalar_one_or_none()


async def fetch_active_outbound_endpoints(
    session: AsyncSession,
    *,
    partner_id: int,
    event_type: str,
) -> list[PartnerEndpoint]:
    result = await session.execute(
        select(PartnerEndpoint).where(
            PartnerEndpoint.partner_id == partner_id,
            PartnerEndpoint.direction == EndpointDirection.OUTBOUND,
            PartnerEndpoint.status == EndpointStatus.ACTIVE,
            PartnerEndpoint.event_types.contains([event_type]),
        )
    )
    return list(result.scalars().all())


async def fetch_deliveries_by_source_event_id(
    session: AsyncSession,
    *,
    partner_id: int,
    source_event_id: str,
) -> list[Delivery]:
    result = await session.execute(
        select(Delivery).where(
            Delivery.partner_id == partner_id,
            Delivery.source_event_id == source_event_id,
        )
    )
    return list(result.scalars().all())


async def fetch_delivery_by_idempotency(
    session: AsyncSession,
    *,
    partner_id: int,
    idempotency_key: str,
) -> Delivery | None:
    result = await session.execute(
        select(Delivery).where(
            Delivery.partner_id == partner_id,
            Delivery.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


def build_pending_delivery(
    *,
    partner: Partner,
    endpoint: PartnerEndpoint,
    event_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    source_event_id: str,
    correlation_id: str,
    now: datetime,
) -> Delivery:
    return Delivery(
        partner_id=partner.id,
        endpoint_id=endpoint.id,
        direction=DeliveryDirection.OUTBOUND,
        event_type=event_type,
        idempotency_key=idempotency_key,
        source_event_id=source_event_id,
        payload=payload,
        payload_hash=canonical_payload_hash(payload),
        status=DeliveryStatus.PENDING,
        attempt_count=0,
        max_attempts=endpoint.max_attempts,
        sla_deadline_at=compute_sla_deadline(now, partner=partner, endpoint=endpoint),
        correlation_id=correlation_id,
    )


def utc_now_from_timestamp(now_ts: int) -> datetime:
    return datetime.fromtimestamp(now_ts, tz=UTC)

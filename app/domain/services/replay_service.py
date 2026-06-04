"""Manual delivery replay (spec §7.1.3, ADR-001, ADR-008)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.enums import DeliveryStatus
from app.domain.errors import DeliveryNotFoundError, DeliveryNotReplayableError
from app.domain.models.audit import AuditLog
from app.domain.models.delivery import Delivery
from app.domain.models.partner import Partner
from app.domain.services.circuit_breaker import is_open
from app.domain.services.outbox import enqueue_outbox
from app.domain.services.rate_limit import allow_request
from app.domain.services.status_machine import transition
from app.integrations.kafka_producer import (
    OUTBOUND_PENDING_TOPIC,
    build_outbound_pending_envelope,
)
from app.observability.metrics import record_delivery_metric


async def fetch_delivery_with_partner(
    session: AsyncSession,
    delivery_public_id: uuid.UUID,
) -> tuple[Delivery, Partner] | None:
    result = await session.execute(
        select(Delivery, Partner)
        .join(Partner, Delivery.partner_id == Partner.id)
        .where(Delivery.public_id == delivery_public_id)
    )
    row = result.one_or_none()
    if row is None:
        return None
    delivery, partner = row
    return delivery, partner


def _record_invalid_transition(partner_slug: str | None) -> None:
    attributes: dict[str, str] = {}
    if partner_slug is not None:
        attributes["partner_slug"] = partner_slug
    record_delivery_metric("hub_invalid_transition_total", attributes=attributes)


def _next_kafka_attempt(*, attempt_count: int, reset_attempt_counter: bool) -> int:
    if reset_attempt_counter:
        return 1
    return max(attempt_count + 1, 1)


async def replay_delivery(
    session: AsyncSession,
    *,
    delivery_public_id: uuid.UUID,
    actor_id: str,
    reason: str,
    reset_attempt_counter: bool,
    trigger: Literal["manual", "scheduled"] = "manual",
) -> Delivery:
    row = await fetch_delivery_with_partner(session, delivery_public_id)
    if row is None:
        raise DeliveryNotFoundError()
    delivery, partner = row

    current = DeliveryStatus(delivery.status)
    if current is not DeliveryStatus.FAILED:
        _record_invalid_transition(partner.slug)
        raise DeliveryNotReplayableError(current.value)

    def _on_invalid(_src: DeliveryStatus, _dst: DeliveryStatus) -> None:
        _record_invalid_transition(partner.slug)

    delivery.status = transition(
        current,
        DeliveryStatus.REPLAYING,
        on_invalid=_on_invalid,
    ).value

    if reset_attempt_counter:
        delivery.attempt_count = 0

    kafka_attempt = _next_kafka_attempt(
        attempt_count=delivery.attempt_count,
        reset_attempt_counter=reset_attempt_counter,
    )

    audit = AuditLog(
        actor_id=actor_id,
        action="delivery.replay",
        resource_type="delivery",
        resource_id=delivery.public_id,
        metadata_={"reason": reason, "trigger": trigger},
    )
    session.add(audit)

    scheduled_at = datetime.now(UTC)
    envelope = build_outbound_pending_envelope(
        delivery_public_id=delivery.public_id,
        partner_public_id=partner.public_id,
        endpoint_id=delivery.endpoint_id,
        event_type=delivery.event_type,
        payload=delivery.payload,
        idempotency_key=delivery.idempotency_key,
        correlation_id=delivery.correlation_id,
        scheduled_at=scheduled_at,
        sla_deadline_at=delivery.sla_deadline_at,
        attempt=kafka_attempt,
    )
    enqueue_outbox(
        session,
        aggregate_type="delivery",
        aggregate_id=delivery.id,
        topic=OUTBOUND_PENDING_TOPIC,
        payload=envelope,
        key=str(partner.public_id),
    )

    await session.commit()
    await session.refresh(delivery)

    record_delivery_metric(
        "hub_replay_total",
        attributes={"trigger": trigger, "partner_slug": partner.slug},
    )

    return delivery


class BulkReplayResult:
    """Per-delivery bulk replay categorization."""

    __slots__ = (
        "requested",
        "replayed",
        "skipped_open_circuit",
        "skipped_rate_limited",
        "skipped_invalid_status",
        "not_found",
    )

    def __init__(self, requested: list[uuid.UUID]) -> None:
        self.requested = requested
        self.replayed: list[uuid.UUID] = []
        self.skipped_open_circuit: list[uuid.UUID] = []
        self.skipped_rate_limited: list[uuid.UUID] = []
        self.skipped_invalid_status: list[uuid.UUID] = []
        self.not_found: list[uuid.UUID] = []


async def bulk_replay_deliveries(
    session: AsyncSession,
    *,
    delivery_public_ids: list[uuid.UUID],
    actor_id: str,
    reason: str,
    redis: Redis | None,
    settings: Settings,
) -> BulkReplayResult:
    """Replay multiple failed deliveries; per-id skips without failing the batch."""
    result = BulkReplayResult(requested=list(delivery_public_ids))

    for delivery_public_id in delivery_public_ids:
        row = await fetch_delivery_with_partner(session, delivery_public_id)
        if row is None:
            result.not_found.append(delivery_public_id)
            continue

        delivery, partner = row
        current = DeliveryStatus(delivery.status)
        if current is not DeliveryStatus.FAILED:
            result.skipped_invalid_status.append(delivery_public_id)
            continue

        if await is_open(redis, partner_slug=partner.slug, settings=settings):
            result.skipped_open_circuit.append(delivery_public_id)
            continue

        if not await allow_request(
            redis,
            partner_slug=partner.slug,
            rate_limit_rps=partner.rate_limit_rps,
        ):
            result.skipped_rate_limited.append(delivery_public_id)
            continue

        await replay_delivery(
            session,
            delivery_public_id=delivery_public_id,
            actor_id=actor_id,
            reason=reason,
            reset_attempt_counter=False,
        )
        result.replayed.append(delivery_public_id)

    return result

"""Transactional outbox enqueue (ADR-007 Stage 2 persist path)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Insert, column, insert, literal, select, values
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ids import generate_uuidv7
from app.domain.models.delivery import Delivery
from app.domain.models.outbox import OutboxEvent


def _ensure_public_id(delivery: Delivery) -> None:
    if delivery.public_id is None:
        delivery.public_id = generate_uuidv7()


def _delivery_insert_row(delivery: Delivery) -> dict[str, Any]:
    _ensure_public_id(delivery)
    return {
        "public_id": delivery.public_id,
        "partner_id": delivery.partner_id,
        "endpoint_id": delivery.endpoint_id,
        "direction": delivery.direction,
        "event_type": delivery.event_type,
        "idempotency_key": delivery.idempotency_key,
        "payload": delivery.payload,
        "payload_hash": delivery.payload_hash,
        "status": delivery.status,
        "attempt_count": delivery.attempt_count,
        "max_attempts": delivery.max_attempts,
        "next_retry_at": delivery.next_retry_at,
        "first_success_at": delivery.first_success_at,
        "sla_deadline_at": delivery.sla_deadline_at,
        "sla_breached": bool(delivery.sla_breached),
        "last_error_code": delivery.last_error_code,
        "last_error_message": delivery.last_error_message,
        "correlation_id": delivery.correlation_id,
        "source_event_id": delivery.source_event_id,
    }


def build_delivery_outbox_persist_stmt(
    *,
    deliveries: list[Delivery],
    envelopes: list[dict[str, Any]],
    message_key: str,
    topic: str,
) -> Insert:
    """One PostgreSQL statement: INSERT deliveries … RETURNING, then INSERT outbox."""
    if len(deliveries) != len(envelopes):
        raise ValueError("envelopes count must match deliveries")
    if not deliveries:
        raise ValueError("deliveries must not be empty")

    rows = [_delivery_insert_row(delivery) for delivery in deliveries]
    inserted = (
        insert(Delivery)
        .values(rows)
        .returning(Delivery.id, Delivery.public_id)
        .cte("ins_deliveries")
    )
    envelope_rows = values(
        column("public_id", PG_UUID(as_uuid=True)),
        column("payload", JSONB),
        name="outbox_envelopes",
    ).data(
        [
            (delivery.public_id, envelope)
            for delivery, envelope in zip(deliveries, envelopes, strict=True)
        ]
    )
    return insert(OutboxEvent).from_select(
        [
            "aggregate_type",
            "aggregate_id",
            "topic",
            "message_key",
            "payload",
        ],
        select(
            literal("delivery"),
            inserted.c.id,
            literal(topic),
            literal(message_key),
            envelope_rows.c.payload,
        ).select_from(
            inserted.join(
                envelope_rows,
                inserted.c.public_id == envelope_rows.c.public_id,
            )
        ),
        include_defaults=False,  # alembic server_default; True binds publish_attempts=NULL
    )


async def persist_deliveries_and_outbox(
    session: AsyncSession,
    *,
    deliveries: list[Delivery],
    envelopes: list[dict[str, Any]],
    message_key: str,
    topic: str,
) -> None:
    """Insert deliveries and matching outbox rows in one round-trip, same transaction."""
    stmt = build_delivery_outbox_persist_stmt(
        deliveries=deliveries,
        envelopes=envelopes,
        message_key=message_key,
        topic=topic,
    )
    await session.execute(stmt)


def enqueue_outbox(
    session: AsyncSession,
    *,
    aggregate_type: str,
    aggregate_id: int,
    topic: str,
    payload: dict[str, object],
    key: str,
) -> OutboxEvent:
    event = OutboxEvent(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        topic=topic,
        payload=payload,
        message_key=key,
    )
    session.add(event)
    return event

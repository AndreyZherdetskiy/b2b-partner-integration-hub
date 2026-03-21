"""AIOKafka producer for inbound and outbound event publishing."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from aiokafka import AIOKafkaProducer

from app.config import Settings
from app.domain.services.retry_topics import OUTBOUND_RETRY_30S_TOPIC
from app.observability.trace_context import kafka_trace_headers


def create_kafka_producer(settings: Settings) -> AIOKafkaProducer:
    return AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k is not None else None,
    )


def inbound_topic(event_type: str) -> str:
    return f"hub.inbound.{event_type}"


OUTBOUND_PENDING_TOPIC = "hub.outbound.pending"
OUTBOUND_DLQ_TOPIC = "hub.outbound.dlq"
SLA_BREACHED_TOPIC = "hub.integration.sla_breached"


def build_inbound_envelope(
    *,
    event_id: UUID,
    partner_public_id: UUID,
    event_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    correlation_id: str,
    received_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": str(event_id),
        "partner_id": str(partner_public_id),
        "event_type": event_type,
        "payload": payload,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "received_at": received_at.isoformat(),
    }


def build_outbound_pending_envelope(
    *,
    delivery_public_id: UUID,
    partner_public_id: UUID,
    endpoint_id: UUID,
    event_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    correlation_id: str,
    scheduled_at: datetime,
    sla_deadline_at: datetime,
    attempt: int = 1,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "delivery_id": str(delivery_public_id),
        "partner_id": str(partner_public_id),
        "endpoint_id": str(endpoint_id),
        "event_type": event_type,
        "attempt": attempt,
        "payload": payload,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "scheduled_at": scheduled_at.isoformat(),
        "sla_deadline_at": sla_deadline_at.isoformat(),
    }


def build_outbound_retry_envelope(
    *,
    delivery_public_id: UUID,
    partner_public_id: UUID,
    endpoint_id: UUID,
    event_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    correlation_id: str,
    scheduled_at: datetime,
    sla_deadline_at: datetime,
    attempt: int,
) -> dict[str, Any]:
    return build_outbound_pending_envelope(
        delivery_public_id=delivery_public_id,
        partner_public_id=partner_public_id,
        endpoint_id=endpoint_id,
        event_type=event_type,
        payload=payload,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        scheduled_at=scheduled_at,
        sla_deadline_at=sla_deadline_at,
        attempt=attempt,
    )


def _outbound_headers(
    *,
    correlation_id: str,
    delivery_public_id: UUID,
    event_type: str,
    attempt: int,
) -> list[tuple[str, bytes]]:
    return [
        ("correlation_id", correlation_id.encode("utf-8")),
        ("delivery_id", str(delivery_public_id).encode("utf-8")),
        ("event_type", event_type.encode("utf-8")),
        ("attempt", str(attempt).encode("utf-8")),
        ("content-type", b"application/json"),
        *kafka_trace_headers(),
    ]


async def publish_outbound_retry(
    producer: AIOKafkaProducer,
    *,
    topic: str,
    delivery_public_id: UUID,
    partner_public_id: UUID,
    endpoint_id: UUID,
    event_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    correlation_id: str,
    scheduled_at: datetime,
    sla_deadline_at: datetime,
    attempt: int,
) -> None:
    envelope = build_outbound_retry_envelope(
        delivery_public_id=delivery_public_id,
        partner_public_id=partner_public_id,
        endpoint_id=endpoint_id,
        event_type=event_type,
        payload=payload,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        scheduled_at=scheduled_at,
        sla_deadline_at=sla_deadline_at,
        attempt=attempt,
    )
    await producer.send_and_wait(
        topic,
        key=str(partner_public_id),
        value=envelope,
        headers=_outbound_headers(
            correlation_id=correlation_id,
            delivery_public_id=delivery_public_id,
            event_type=event_type,
            attempt=attempt,
        ),
    )


async def publish_outbound_retry_30s(
    producer: AIOKafkaProducer,
    *,
    delivery_public_id: UUID,
    partner_public_id: UUID,
    endpoint_id: UUID,
    event_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    correlation_id: str,
    scheduled_at: datetime,
    sla_deadline_at: datetime,
    attempt: int,
) -> None:
    await publish_outbound_retry(
        producer,
        topic=OUTBOUND_RETRY_30S_TOPIC,
        delivery_public_id=delivery_public_id,
        partner_public_id=partner_public_id,
        endpoint_id=endpoint_id,
        event_type=event_type,
        payload=payload,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        scheduled_at=scheduled_at,
        sla_deadline_at=sla_deadline_at,
        attempt=attempt,
    )


def build_dlq_envelope(
    *,
    delivery_public_id: UUID,
    partner_public_id: UUID,
    endpoint_id: UUID,
    event_type: str,
    reason: str,
    correlation_id: str,
    attempt: int,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "delivery_id": str(delivery_public_id),
        "partner_id": str(partner_public_id),
        "endpoint_id": str(endpoint_id),
        "event_type": event_type,
        "attempt": attempt,
        "reason": reason,
        "correlation_id": correlation_id,
    }


async def publish_outbound_dlq(
    producer: AIOKafkaProducer,
    *,
    delivery_public_id: UUID,
    partner_public_id: UUID,
    endpoint_id: UUID,
    event_type: str,
    reason: str,
    correlation_id: str,
    attempt: int,
) -> None:
    envelope = build_dlq_envelope(
        delivery_public_id=delivery_public_id,
        partner_public_id=partner_public_id,
        endpoint_id=endpoint_id,
        event_type=event_type,
        reason=reason,
        correlation_id=correlation_id,
        attempt=attempt,
    )
    await producer.send_and_wait(
        OUTBOUND_DLQ_TOPIC,
        key=str(partner_public_id),
        value=envelope,
        headers=_outbound_headers(
            correlation_id=correlation_id,
            delivery_public_id=delivery_public_id,
            event_type=event_type,
            attempt=attempt,
        ),
    )


def build_sla_breached_envelope(
    *,
    delivery_public_id: UUID,
    partner_public_id: UUID,
    event_type: str,
    correlation_id: str,
    sla_deadline_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "delivery_id": str(delivery_public_id),
        "partner_id": str(partner_public_id),
        "event_type": event_type,
        "correlation_id": correlation_id,
        "sla_deadline_at": sla_deadline_at.isoformat(),
    }


async def publish_sla_breached(
    producer: AIOKafkaProducer,
    *,
    delivery_public_id: UUID,
    partner_public_id: UUID,
    event_type: str,
    correlation_id: str,
    sla_deadline_at: datetime,
) -> None:
    envelope = build_sla_breached_envelope(
        delivery_public_id=delivery_public_id,
        partner_public_id=partner_public_id,
        event_type=event_type,
        correlation_id=correlation_id,
        sla_deadline_at=sla_deadline_at,
    )
    await producer.send_and_wait(
        SLA_BREACHED_TOPIC,
        key=str(partner_public_id),
        value=envelope,
        headers=[
            ("correlation_id", correlation_id.encode("utf-8")),
            ("delivery_id", str(delivery_public_id).encode("utf-8")),
            ("event_type", event_type.encode("utf-8")),
            ("content-type", b"application/json"),
            *kafka_trace_headers(),
        ],
    )

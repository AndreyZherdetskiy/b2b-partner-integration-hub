"""Outbound delivery processing — HTTP POST, retry, DLQ (spec §4.7, ADR-002, ADR-008)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import httpx
from aiokafka import AIOKafkaProducer
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.enums import DeadLetterReason, DeliveryStatus
from app.domain.models.attempt import DeliveryAttempt
from app.domain.models.dead_letter import DeadLetter
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.domain.services.backoff import compute_delay_seconds
from app.domain.services.circuit_breaker import allow_outbound, record_failure, record_success
from app.domain.services.delivery_attempt import (
    ResponseClassification,
    classify_http_outcome,
    truncate_response_body,
)
from app.domain.services.retry_topics import retry_topic_for
from app.domain.services.signing_secrets import load_outbound_primary_secret
from app.domain.services.sla_service import apply_first_success
from app.domain.services.status_machine import transition
from app.integrations.http_client import (
    build_outbound_headers,
    post_outbound,
    serialize_payload,
)
from app.integrations.kafka_producer import (
    publish_outbound_dlq,
    publish_outbound_retry,
    publish_sla_breached,
)
from app.observability.metrics import record_delivery_metric

logger = logging.getLogger(__name__)

_DELIVERING_SOURCES = frozenset(
    {
        DeliveryStatus.PENDING,
        DeliveryStatus.RETRYING,
        DeliveryStatus.REPLAYING,
    }
)


class ProcessOutcome(StrEnum):
    SKIPPED = "skipped"
    DELIVERED = "delivered"
    RETRY_SCHEDULED = "retry_scheduled"
    DLQ = "dlq"


async def fetch_delivery_context(
    session: AsyncSession,
    delivery_public_id: uuid.UUID,
) -> tuple[Delivery, Partner, PartnerEndpoint] | None:
    result = await session.execute(
        select(Delivery, Partner, PartnerEndpoint)
        .join(Partner, Delivery.partner_id == Partner.id)
        .join(PartnerEndpoint, Delivery.endpoint_id == PartnerEndpoint.id)
        .where(Delivery.public_id == delivery_public_id)
    )
    row = result.one_or_none()
    if row is None:
        return None
    return row[0], row[1], row[2]


def _record_invalid_transition(partner_slug: str) -> None:
    record_delivery_metric(
        "hub_invalid_transition_total",
        attributes={"partner_slug": partner_slug},
    )


def _network_error_from_result(error_type: str | None) -> BaseException | None:
    if error_type is None:
        return None
    if error_type == "timeout":
        return httpx.TimeoutException("timeout")
    if error_type == "connect_error":
        return httpx.ConnectError("connect")
    return httpx.NetworkError("network")


async def process_outbound_message(
    session: AsyncSession,
    producer: AIOKafkaProducer,
    envelope: dict[str, Any],
    settings: Settings,
    *,
    now: datetime | None = None,
    http_client: httpx.AsyncClient | None = None,
    redis: Redis | None = None,
) -> ProcessOutcome:
    current_time = now or datetime.now(UTC)
    delivery_public_id = uuid.UUID(envelope["delivery_id"])

    context = await fetch_delivery_context(session, delivery_public_id)
    if context is None:
        logger.warning("delivery_not_found", extra={"delivery_id": str(delivery_public_id)})
        return ProcessOutcome.SKIPPED

    delivery, partner, endpoint = context
    current_status = DeliveryStatus(delivery.status)

    if current_status in {DeliveryStatus.DELIVERED, DeliveryStatus.FAILED}:
        return ProcessOutcome.SKIPPED

    if current_status not in _DELIVERING_SOURCES:
        _record_invalid_transition(partner.slug)
        return ProcessOutcome.SKIPPED

    def _on_invalid(_src: DeliveryStatus, _dst: DeliveryStatus) -> None:
        _record_invalid_transition(partner.slug)

    delivery.status = transition(
        current_status,
        DeliveryStatus.DELIVERING,
        on_invalid=_on_invalid,
    ).value

    signing_secret = await load_outbound_primary_secret(session, partner, settings)
    if signing_secret is None:
        logger.error("missing_signing_secret", extra={"partner_slug": partner.slug})
        await _mark_failed_dlq(
            session,
            producer,
            delivery=delivery,
            partner=partner,
            endpoint=endpoint,
            reason=DeadLetterReason.NON_RETRYABLE_ERROR,
            last_http_status=None,
            last_error_message="missing signing secret",
        )
        return ProcessOutcome.DLQ

    body_bytes = serialize_payload(delivery.payload)
    timestamp = str(int(current_time.timestamp()))
    headers = build_outbound_headers(
        delivery_public_id=str(delivery.public_id),
        event_type=delivery.event_type,
        timestamp=timestamp,
        body_bytes=body_bytes,
        signing_secret=signing_secret,
        idempotency_key=delivery.idempotency_key,
        correlation_id=delivery.correlation_id,
    )

    if not await allow_outbound(redis, partner_slug=partner.slug, settings=settings):
        delay_seconds = (
            float(settings.hub_backoff_base_seconds)
            if delivery.attempt_count < 1
            else compute_delay_seconds(
                delivery.attempt_count,
                base=settings.hub_backoff_base_seconds,
                multiplier=settings.hub_backoff_multiplier,
                max_seconds=settings.hub_backoff_max_seconds,
                rng=lambda: 0.0,
            )
        )
        next_attempt = delivery.attempt_count + 1
        retry_topic = retry_topic_for(next_attempt, delay_seconds)
        delivery.next_retry_at = current_time + timedelta(seconds=delay_seconds)
        delivery.status = transition(DeliveryStatus.DELIVERING, DeliveryStatus.RETRYING).value
        await session.commit()

        await publish_outbound_retry(
            producer,
            topic=retry_topic,
            delivery_public_id=delivery.public_id,
            partner_public_id=partner.public_id,
            endpoint_id=delivery.endpoint_id,
            event_type=delivery.event_type,
            payload=delivery.payload,
            idempotency_key=delivery.idempotency_key,
            correlation_id=delivery.correlation_id,
            scheduled_at=delivery.next_retry_at,
            sla_deadline_at=delivery.sla_deadline_at,
            attempt=next_attempt,
        )
        return ProcessOutcome.RETRY_SCHEDULED

    attempt_number = delivery.attempt_count + 1
    requested_at = current_time

    post_result = await post_outbound(
        url=endpoint.url,
        body_bytes=body_bytes,
        headers=headers,
        timeout_connect_s=endpoint.timeout_connect_ms / 1000.0,
        timeout_read_s=endpoint.timeout_read_ms / 1000.0,
        client=http_client,
    )

    responded_at = datetime.now(UTC)
    attempt = DeliveryAttempt(
        delivery_id=delivery.id,
        attempt_number=attempt_number,
        requested_at=requested_at,
        responded_at=responded_at,
        http_status_code=post_result.http_status_code,
        response_headers=post_result.response_headers,
        response_body=truncate_response_body(post_result.response_body),
        error_type=post_result.error_type,
        duration_ms=post_result.duration_ms,
    )
    session.add(attempt)

    network_error = _network_error_from_result(post_result.error_type)
    classification = classify_http_outcome(
        post_result.http_status_code,
        retry_on_status_codes=list(endpoint.retry_on_status_codes),
        error=network_error,
    )

    if classification == ResponseClassification.SUCCESS:
        await record_success(redis, partner_slug=partner.slug, settings=settings)
        delivery.status = transition(DeliveryStatus.DELIVERING, DeliveryStatus.DELIVERED).value
        prior_sla_breached = delivery.sla_breached
        first_success_at, sla_breached = apply_first_success(
            current_time,
            delivery.sla_deadline_at,
            delivery.first_success_at,
            delivery.sla_breached,
        )
        delivery.first_success_at = first_success_at
        delivery.sla_breached = sla_breached
        delivery.last_error_code = None
        delivery.last_error_message = None
        await session.commit()

        if sla_breached and not prior_sla_breached:
            record_delivery_metric(
                "hub_sla_breaches_total",
                attributes={"partner_slug": partner.slug},
            )
            await publish_sla_breached(
                producer,
                delivery_public_id=delivery.public_id,
                partner_public_id=partner.public_id,
                event_type=delivery.event_type,
                correlation_id=delivery.correlation_id,
                sla_deadline_at=delivery.sla_deadline_at,
            )

        return ProcessOutcome.DELIVERED

    if classification == ResponseClassification.POISON:
        error_message = post_result.response_body or post_result.error_type or "non-retryable"
        delivery.last_error_code = str(post_result.http_status_code or "poison")
        delivery.last_error_message = truncate_response_body(error_message)
        await _mark_failed_dlq(
            session,
            producer,
            delivery=delivery,
            partner=partner,
            endpoint=endpoint,
            reason=DeadLetterReason.NON_RETRYABLE_ERROR,
            last_http_status=post_result.http_status_code,
            last_error_message=delivery.last_error_message,
        )
        return ProcessOutcome.DLQ

    await record_failure(redis, partner_slug=partner.slug, settings=settings)
    delivery.attempt_count += 1
    error_code = post_result.http_status_code or post_result.error_type or "retry"
    delivery.last_error_code = str(error_code)
    delivery.last_error_message = truncate_response_body(
        post_result.response_body or post_result.error_type or "retryable failure",
    )

    if delivery.attempt_count < delivery.max_attempts:
        delay_seconds = compute_delay_seconds(
            delivery.attempt_count,
            base=settings.hub_backoff_base_seconds,
            multiplier=settings.hub_backoff_multiplier,
            max_seconds=settings.hub_backoff_max_seconds,
            rng=lambda: 0.0,
        )
        next_attempt = delivery.attempt_count + 1
        retry_topic = retry_topic_for(next_attempt, delay_seconds)
        delivery.next_retry_at = current_time + timedelta(seconds=delay_seconds)
        delivery.status = transition(DeliveryStatus.DELIVERING, DeliveryStatus.RETRYING).value
        await session.commit()

        await publish_outbound_retry(
            producer,
            topic=retry_topic,
            delivery_public_id=delivery.public_id,
            partner_public_id=partner.public_id,
            endpoint_id=delivery.endpoint_id,
            event_type=delivery.event_type,
            payload=delivery.payload,
            idempotency_key=delivery.idempotency_key,
            correlation_id=delivery.correlation_id,
            scheduled_at=delivery.next_retry_at,
            sla_deadline_at=delivery.sla_deadline_at,
            attempt=next_attempt,
        )
        return ProcessOutcome.RETRY_SCHEDULED

    await _mark_failed_dlq(
        session,
        producer,
        delivery=delivery,
        partner=partner,
        endpoint=endpoint,
        reason=DeadLetterReason.MAX_ATTEMPTS_EXCEEDED,
        last_http_status=post_result.http_status_code,
        last_error_message=delivery.last_error_message or "max attempts exceeded",
    )
    return ProcessOutcome.DLQ


async def _mark_failed_dlq(
    session: AsyncSession,
    producer: AIOKafkaProducer,
    *,
    delivery: Delivery,
    partner: Partner,
    endpoint: PartnerEndpoint,
    reason: DeadLetterReason,
    last_http_status: int | None,
    last_error_message: str,
) -> None:
    delivery.status = transition(DeliveryStatus.DELIVERING, DeliveryStatus.FAILED).value
    delivery.last_error_message = truncate_response_body(last_error_message)

    dead_letter = DeadLetter(
        delivery_id=delivery.id,
        partner_id=partner.id,
        reason=reason.value,
        last_http_status=last_http_status,
        last_error_message=truncate_response_body(last_error_message),
    )
    session.add(dead_letter)
    await session.commit()

    record_delivery_metric(
        "hub_dlq_messages_total",
        attributes={"partner_slug": partner.slug},
    )

    await publish_outbound_dlq(
        producer,
        delivery_public_id=delivery.public_id,
        partner_public_id=partner.public_id,
        endpoint_id=endpoint.id,
        event_type=delivery.event_type,
        reason=reason.value,
        correlation_id=delivery.correlation_id,
        attempt=delivery.attempt_count + 1,
    )

"""Map ORM models to admin response schemas."""

from __future__ import annotations

import uuid

from app.domain.enums import DeliveryStatus, EndpointDirection, EndpointStatus, PartnerStatus
from app.domain.models.attempt import DeliveryAttempt
from app.domain.models.dead_letter import DeadLetter
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.schemas.delivery import DeadLetterResponse, DeliveryAttemptResponse, DeliveryResponse
from app.schemas.endpoint import EndpointResponse
from app.schemas.partner import PartnerResponse


def partner_to_response(partner: Partner) -> PartnerResponse:
    return PartnerResponse(
        id=partner.public_id,
        slug=partner.slug,
        name=partner.name,
        status=PartnerStatus(partner.status),
        sla_seconds=partner.sla_seconds,
        rate_limit_rps=partner.rate_limit_rps,
        auto_replay_enabled=partner.auto_replay_enabled,
        circuit_breaker_config=partner.circuit_breaker_config,
        created_at=partner.created_at,
        updated_at=partner.updated_at,
    )


def attempt_to_response(attempt: DeliveryAttempt) -> DeliveryAttemptResponse:
    return DeliveryAttemptResponse(
        id=attempt.id,
        attempt_number=attempt.attempt_number,
        requested_at=attempt.requested_at,
        responded_at=attempt.responded_at,
        http_status_code=attempt.http_status_code,
        error_type=attempt.error_type,
        duration_ms=attempt.duration_ms,
        created_at=attempt.created_at,
    )


def delivery_to_response(
    delivery: Delivery,
    *,
    partner_public_id: uuid.UUID,
    attempts: list[DeliveryAttemptResponse] | None = None,
) -> DeliveryResponse:
    return DeliveryResponse(
        id=delivery.public_id,
        partner_id=partner_public_id,
        endpoint_id=delivery.endpoint_id,
        event_type=delivery.event_type,
        status=DeliveryStatus(delivery.status),
        attempt_count=delivery.attempt_count,
        max_attempts=delivery.max_attempts,
        idempotency_key=delivery.idempotency_key,
        payload=delivery.payload,
        correlation_id=delivery.correlation_id,
        sla_deadline_at=delivery.sla_deadline_at,
        sla_breached=delivery.sla_breached,
        first_success_at=delivery.first_success_at,
        last_error_code=delivery.last_error_code,
        last_error_message=delivery.last_error_message,
        created_at=delivery.created_at,
        updated_at=delivery.updated_at,
        attempts=attempts or [],
    )


def dead_letter_to_response(
    dead_letter: DeadLetter,
    *,
    delivery_public_id: uuid.UUID,
    partner_public_id: uuid.UUID,
) -> DeadLetterResponse:
    return DeadLetterResponse(
        id=dead_letter.id,
        delivery_id=delivery_public_id,
        partner_id=partner_public_id,
        reason=dead_letter.reason,
        last_http_status=dead_letter.last_http_status,
        last_error_message=dead_letter.last_error_message,
        acknowledged_at=dead_letter.acknowledged_at,
        acknowledged_by=dead_letter.acknowledged_by,
        created_at=dead_letter.created_at,
    )


def endpoint_to_response(
    endpoint: PartnerEndpoint,
    partner_public_id: uuid.UUID,
) -> EndpointResponse:
    return EndpointResponse(
        id=endpoint.id,
        partner_id=partner_public_id,
        direction=EndpointDirection(endpoint.direction),
        url=endpoint.url,
        event_types=list(endpoint.event_types),
        status=EndpointStatus(endpoint.status),
        sla_seconds=endpoint.sla_seconds,
        max_attempts=endpoint.max_attempts,
        timeout_connect_ms=endpoint.timeout_connect_ms,
        timeout_read_ms=endpoint.timeout_read_ms,
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
    )

"""Shared outbound delivery enqueue (internal API and admin sandbox)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import PartnerStatus
from app.domain.errors import (
    IdempotencyConflictError,
    NoActiveEndpointError,
    PartnerInactiveError,
    PartnerNotFoundError,
    SchemaValidationFailedError,
)
from app.domain.ids import generate_uuidv7
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.domain.services.accept_path_cache import (
    cache_get,
    cache_schema_value,
    cache_set,
    copy_endpoint,
    copy_partner,
    endpoints_cache_key,
    partner_cache_key,
    schema_cache_key,
    schema_from_cache,
)
from app.domain.services.delivery_service import (
    build_pending_delivery,
    derived_idempotency_key,
    fetch_active_outbound_endpoints,
    fetch_deliveries_by_source_event_id,
    fetch_delivery_by_idempotency,
    fetch_partner_by_public_id,
)
from app.domain.services.outbox import persist_deliveries_and_outbox
from app.domain.services.schema_registry import (
    SchemaValidationError,
    fetch_latest_active_schema,
    validate_payload,
)
from app.integrations.kafka_producer import (
    OUTBOUND_PENDING_TOPIC,
    build_outbound_pending_envelope,
)
from app.schemas.outbound import (
    OutboundEventAcceptedResponse,
    OutboundEventDuplicateResponse,
)


def duplicate_response(deliveries: list[Delivery]) -> OutboundEventDuplicateResponse:
    delivery_ids = [delivery.public_id for delivery in deliveries]
    return OutboundEventDuplicateResponse(
        delivery_id=delivery_ids[0],
        delivery_ids=delivery_ids,
        status="duplicate",
    )


async def enqueue_outbound_for_event(
    session: AsyncSession,
    *,
    partner_id: UUID,
    event_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    correlation_id: str,
    now: datetime,
) -> OutboundEventAcceptedResponse | OutboundEventDuplicateResponse:
    """Validate, fan-out, persist deliveries and outbox rows in one commit."""
    partner_key = partner_cache_key(partner_id)
    cached_partner = cache_get(partner_key)
    if cached_partner is None:
        partner = await fetch_partner_by_public_id(session, partner_id)
        if partner is not None:
            cache_set(partner_key, copy_partner(partner))
    else:
        partner = cast(Partner, cached_partner)
    if partner is None:
        raise PartnerNotFoundError()
    if partner.status != PartnerStatus.ACTIVE:
        raise PartnerInactiveError()

    schema_key = schema_cache_key(event_type)
    cached_schema = cache_get(schema_key)
    if cached_schema is None:
        schema_row = await fetch_latest_active_schema(session, event_type)
        cache_set(schema_key, cache_schema_value(schema_row))
    else:
        schema_row = schema_from_cache(cached_schema)
    try:
        validate_payload(event_type, payload, schema_row)
    except SchemaValidationError:
        raise SchemaValidationFailedError() from None

    endpoints_key = endpoints_cache_key(partner.id, event_type)
    cached_endpoints = cache_get(endpoints_key)
    if cached_endpoints is None:
        endpoints = await fetch_active_outbound_endpoints(
            session,
            partner_id=partner.id,
            event_type=event_type,
        )
        cache_set(endpoints_key, [copy_endpoint(ep) for ep in endpoints])
    else:
        endpoints = cast(list[PartnerEndpoint], cached_endpoints)
    if not endpoints:
        raise NoActiveEndpointError(event_type)

    client_key = idempotency_key
    created_deliveries: list[Delivery] = []

    for endpoint in endpoints:
        delivery = build_pending_delivery(
            partner=partner,
            endpoint=endpoint,
            event_type=event_type,
            payload=payload,
            idempotency_key=derived_idempotency_key(client_key, endpoint.public_id),
            source_event_id=client_key,
            correlation_id=correlation_id,
            now=now,
        )
        if delivery.public_id is None:
            delivery.public_id = generate_uuidv7()
        created_deliveries.append(delivery)

    envelopes = [
        build_outbound_pending_envelope(
            delivery_public_id=delivery.public_id,
            partner_public_id=partner.public_id,
            endpoint_id=endpoint.id,
            event_type=event_type,
            payload=payload,
            idempotency_key=client_key,
            correlation_id=correlation_id,
            scheduled_at=now,
            sla_deadline_at=delivery.sla_deadline_at,
            attempt=1,
        )
        for delivery, endpoint in zip(created_deliveries, endpoints, strict=True)
    ]

    try:
        await persist_deliveries_and_outbox(
            session,
            deliveries=created_deliveries,
            envelopes=envelopes,
            message_key=str(partner.public_id),
            topic=OUTBOUND_PENDING_TOPIC,
        )
    except IntegrityError:
        await session.rollback()
        raced = await fetch_deliveries_by_source_event_id(
            session,
            partner_id=partner.id,
            source_event_id=client_key,
        )
        if raced:
            return duplicate_response(raced)
        conflicting = await fetch_delivery_by_idempotency(
            session,
            partner_id=partner.id,
            idempotency_key=derived_idempotency_key(client_key, endpoints[0].public_id),
        )
        if conflicting is None:
            raise IdempotencyConflictError() from None
        return OutboundEventDuplicateResponse(
            delivery_id=conflicting.public_id,
            delivery_ids=[conflicting.public_id],
            status="duplicate",
        )

    await session.commit()

    delivery_ids: list[UUID] = [delivery.public_id for delivery in created_deliveries]
    return OutboundEventAcceptedResponse(
        delivery_id=delivery_ids[0],
        delivery_ids=delivery_ids,
        status="accepted",
    )

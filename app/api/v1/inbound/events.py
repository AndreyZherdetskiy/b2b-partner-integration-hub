"""Inbound webhook event ingestion (spec §7.1.1, ADR-004, ADR-007 Stage 2)."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession, NowTimestamp, RedisClient
from app.config import Settings, get_settings
from app.domain.enums import PartnerStatus
from app.domain.models.api_key import PartnerApiKey
from app.domain.models.inbound_event import InboundEvent
from app.domain.models.partner import Partner
from app.domain.services import hmac_service
from app.domain.services.api_keys import extract_prefix, verify_api_key
from app.domain.services.idempotency import (
    cache_event_id,
    fetch_inbound_event_by_idempotency,
    get_cached_event_id,
)
from app.domain.services.outbox import enqueue_outbox
from app.domain.services.rate_limit import allow_request
from app.domain.services.schema_registry import (
    SchemaValidationError,
    fetch_latest_active_schema,
    validate_payload,
)
from app.domain.services.signing_secrets import load_inbound_signing_secrets
from app.integrations.kafka_producer import build_inbound_envelope, inbound_topic
from app.logging import get_correlation_id
from app.observability.metrics import record_delivery_metric
from app.schemas.inbound import (
    INBOUND_EVENT_EXAMPLES,
    InboundErrorResponse,
    InboundEventAcceptedResponse,
    InboundEventBody,
    InboundEventDuplicateResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inbound/v1", tags=["inbound"])

ALLOWED_EVENT_TYPES = frozenset({"order.created", "order.updated"})
INBOUND_WRITE_SCOPE = "inbound:write"

_INBOUND_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": InboundErrorResponse, "description": "Missing or invalid API key."},
    403: {
        "model": InboundErrorResponse,
        "description": "Invalid HMAC signature or timestamp skew.",
    },
    404: {"model": InboundErrorResponse, "description": "Partner not found or inactive."},
    409: {
        "model": InboundErrorResponse,
        "description": "Idempotency key conflict while persisting; safe to retry.",
    },
    413: {
        "model": InboundErrorResponse,
        "description": "Request body exceeds the 256 KB payload limit.",
    },
    422: {"model": InboundErrorResponse, "description": "Invalid event schema or event type."},
    429: {"model": InboundErrorResponse, "description": "Partner rate limit exceeded."},
}


def _parse_bearer(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


async def _load_partner(session: AsyncSession, partner_slug: str) -> Partner:
    result = await session.execute(select(Partner).where(Partner.slug == partner_slug))
    partner = result.scalar_one_or_none()
    if partner is None or partner.status != PartnerStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Partner not found or inactive.",
        )
    return partner


def _verify_timestamp(timestamp_header: str | None, now: int, tolerance: int) -> None:
    if timestamp_header is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid HMAC signature or timestamp.",
        )
    try:
        ts = int(timestamp_header)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid HMAC signature or timestamp.",
        ) from exc
    if abs(now - ts) > tolerance:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Request timestamp outside allowed skew window.",
        )


async def _verify_hmac(
    *,
    session: AsyncSession,
    settings: Settings,
    partner: Partner,
    timestamp: str,
    body: bytes,
    signature_header: str | None,
    now: int,
) -> None:
    if signature_header is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid HMAC signature or timestamp.",
        )
    current_time = datetime.fromtimestamp(now, UTC)
    secret, previous_secret = await load_inbound_signing_secrets(
        session,
        partner,
        settings,
        now=current_time,
    )
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid HMAC signature or timestamp.",
        )
    if not hmac_service.verify(
        secret,
        timestamp,
        body,
        signature_header,
        now=now,
        tolerance=settings.hub_inbound_timestamp_tolerance,
        previous_secret=previous_secret,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid HMAC signature.",
        )


async def _verify_api_key(
    session: AsyncSession,
    *,
    partner: Partner,
    api_key: str | None,
) -> None:
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
        )
    prefix = extract_prefix(api_key)
    result = await session.execute(
        select(PartnerApiKey).where(
            PartnerApiKey.key_prefix == prefix,
            PartnerApiKey.partner_id == partner.id,
            PartnerApiKey.revoked_at.is_(None),
        )
    )
    rows = result.scalars().all()
    now_dt = datetime.now(UTC)
    for row in rows:
        if row.expires_at is not None and row.expires_at <= now_dt:
            continue
        if INBOUND_WRITE_SCOPE not in row.scopes:
            continue
        if verify_api_key(api_key, row.key_hash):
            return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid API key.",
    )


def _parse_event_body(body: bytes) -> InboundEventBody:
    try:
        parsed: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid JSON body.",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid JSON body.",
        )
    event_type = parsed.get("event_type")
    payload = parsed.get("payload")
    if not isinstance(event_type, str) or not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid event schema.",
        )
    if event_type not in ALLOWED_EVENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported event_type: {event_type}",
        )
    return InboundEventBody(event_type=event_type, payload=payload)


def _duplicate_json(dup: InboundEventDuplicateResponse) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_200_OK, content=dup.model_dump(mode="json"))


async def _finalize_accepted_inbound(
    redis: Redis | None,
    *,
    partner: Partner,
    inbound: InboundEvent,
    event_type: str,
    idempotency_key: str,
    settings: Settings,
) -> InboundEventAcceptedResponse:
    ttl_seconds = settings.hub_idempotency_ttl_hours * 3600
    await cache_event_id(
        redis,
        partner_id=partner.id,
        idempotency_key=idempotency_key,
        event_id=inbound.id,
        ttl_seconds=ttl_seconds,
    )

    record_delivery_metric(
        "hub_inbound_events_total",
        attributes={"partner_slug": partner.slug, "event_type": event_type},
    )

    return InboundEventAcceptedResponse(event_id=inbound.id, status="accepted")


@router.post(
    "/{partner_slug}/events",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=InboundEventAcceptedResponse,
    responses={
        200: {
            "model": InboundEventDuplicateResponse,
            "description": "Duplicate Idempotency-Key; same event_id returned.",
        },
        **_INBOUND_ERRORS,
    },
    summary="Ingest partner webhook event",
    description=(
        "Accepts a partner webhook. Sign the raw body with HMAC-SHA256 over "
        "`{X-Hub-Timestamp}.{raw_body}` and send the hex digest in `X-Hub-Signature-256` "
        "(optional `sha256=` prefix). First unique `Idempotency-Key` returns **202**; "
        "the same key returns **200** with the original `event_id`. Path `{partner_slug}` "
        "is lowercase kebab-case. JSON `event_id` is a UUIDv7 public identifier."
    ),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": InboundEventBody.model_json_schema(),
                    "examples": INBOUND_EVENT_EXAMPLES,
                }
            },
        }
    },
)
async def ingest_event(
    request: Request,
    partner_slug: Annotated[
        str,
        Path(description="Partner slug identifier (lowercase kebab-case)."),
    ],
    session: DbSession,
    redis: RedisClient,
    now: NowTimestamp,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[
        str | None,
        Header(
            description=(
                "Bearer partner API key with `inbound:write`. Missing or invalid returns 401."
            ),
        ),
    ] = None,
    x_hub_signature_256: Annotated[
        str | None,
        Header(
            alias="X-Hub-Signature-256",
            description=(
                "Required HMAC-SHA256 hex digest of `{timestamp}.{raw_body}` using the partner "
                "signing secret. Optional `sha256=` prefix. Missing or invalid returns 403."
            ),
        ),
    ] = None,
    x_hub_timestamp: Annotated[
        str | None,
        Header(
            alias="X-Hub-Timestamp",
            description=(
                "Required Unix timestamp (seconds) used in the HMAC payload. Skew beyond "
                "the configured tolerance returns 403."
            ),
        ),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description=(
                "Required. First accept is 202; repeating the same key returns 200 with the "
                "original event_id. Missing or blank returns 422."
            ),
        ),
    ] = None,
) -> InboundEventAcceptedResponse | JSONResponse:
    raw_body = await request.body()

    if idempotency_key is None or not idempotency_key.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key header is required.",
        )
    idempotency_key = idempotency_key.strip()

    partner = await _load_partner(session, partner_slug)

    _verify_timestamp(x_hub_timestamp, now, settings.hub_inbound_timestamp_tolerance)
    await _verify_hmac(
        session=session,
        settings=settings,
        partner=partner,
        timestamp=x_hub_timestamp or "",
        body=raw_body,
        signature_header=x_hub_signature_256,
        now=now,
    )

    api_key = _parse_bearer(authorization)
    await _verify_api_key(session, partner=partner, api_key=api_key)

    if not await allow_request(
        redis, partner_slug=partner.slug, rate_limit_rps=partner.rate_limit_rps
    ):
        record_delivery_metric(
            "hub_rate_limit_rejected_total",
            attributes={"partner_slug": partner.slug},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded.",
        )

    event_body = _parse_event_body(raw_body)

    schema_row = await fetch_latest_active_schema(session, event_body.event_type)
    try:
        validate_payload(event_body.event_type, event_body.payload, schema_row)
    except SchemaValidationError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="payload does not match registered schema",
        ) from None

    correlation_id = get_correlation_id()
    if correlation_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Correlation context missing.",
        )
    received_at = datetime.now(UTC)

    cached_id = await get_cached_event_id(
        redis,
        partner_id=partner.id,
        idempotency_key=idempotency_key,
    )
    if cached_id is not None:
        record_delivery_metric(
            "hub_inbound_duplicate_suppressed_total",
            attributes={"partner_slug": partner.slug},
        )
        return _duplicate_json(
            InboundEventDuplicateResponse(event_id=cached_id, status="duplicate")
        )

    payload_hash = hashlib.sha256(raw_body).hexdigest()
    inbound = InboundEvent(
        partner_id=partner.id,
        idempotency_key=idempotency_key,
        event_type=event_body.event_type,
        payload=event_body.payload,
        payload_hash=payload_hash,
        signature_valid=True,
        received_at=received_at,
        correlation_id=correlation_id,
    )
    session.add(inbound)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = await fetch_inbound_event_by_idempotency(
            session,
            partner_id=partner.id,
            idempotency_key=idempotency_key,
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency conflict; retry.",
            ) from None
        record_delivery_metric(
            "hub_inbound_duplicate_suppressed_total",
            attributes={"partner_slug": partner.slug},
        )
        return _duplicate_json(
            InboundEventDuplicateResponse(event_id=existing.id, status="duplicate")
        )

    envelope = build_inbound_envelope(
        event_id=inbound.id,
        partner_public_id=partner.public_id,
        event_type=event_body.event_type,
        payload=event_body.payload,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        received_at=received_at,
    )
    enqueue_outbox(
        session,
        aggregate_type="inbound_event",
        aggregate_id=partner.id,
        topic=inbound_topic(event_body.event_type),
        payload=envelope,
        key=str(partner.public_id),
    )
    await session.commit()

    return await _finalize_accepted_inbound(
        redis,
        partner=partner,
        inbound=inbound,
        event_type=event_body.event_type,
        idempotency_key=idempotency_key,
        settings=settings,
    )

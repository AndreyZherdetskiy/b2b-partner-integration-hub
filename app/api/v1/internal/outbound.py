"""Internal outbound delivery API (spec §7.1.5, ADR-007 Stage 2)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.api.auth import AdminPrincipal, RequireAdmin
from app.api.deps import DbSession, NowTimestamp
from app.domain.services.delivery_service import utc_now_from_timestamp
from app.domain.services.outbound_enqueue import enqueue_outbound_for_event
from app.logging import get_correlation_id
from app.schemas.outbound import (
    OUTBOUND_EVENT_EXAMPLES,
    OutboundErrorResponse,
    OutboundEventAcceptedResponse,
    OutboundEventDuplicateResponse,
    OutboundEventRequest,
)

router = APIRouter(prefix="/internal/v1", tags=["internal"])

_OUTBOUND_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": OutboundErrorResponse, "description": "Missing or invalid admin credentials."},
    403: {
        "model": OutboundErrorResponse,
        "description": "Caller role is not allowed to enqueue outbound deliveries.",
    },
    404: {"model": OutboundErrorResponse, "description": "Partner not found."},
    409: {
        "model": OutboundErrorResponse,
        "description": "Idempotency key conflict while persisting; safe to retry.",
    },
    422: {
        "model": OutboundErrorResponse,
        "description": "Inactive partner, unsupported event type, or invalid correlation_id.",
    },
}


def _duplicate_json(dup: OutboundEventDuplicateResponse) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_200_OK, content=dup.model_dump(mode="json"))


@router.post(
    "/outbound/events",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=OutboundEventAcceptedResponse,
    responses={
        200: {
            "model": OutboundEventDuplicateResponse,
            "description": (
                "Duplicate idempotency_key; same delivery_ids returned without republish."
            ),
        },
        **_OUTBOUND_ERRORS,
    },
    summary="Enqueue outbound partner webhook delivery",
    description=(
        "Platform-only enqueue. `partner_id` is the partner public UUIDv7. "
        "Creates one delivery per matching active endpoint. First unique "
        "`idempotency_key` returns **202**; the same key returns **200** with the "
        "original `delivery_ids`. Not partner-facing."
    ),
)
async def create_outbound_event(
    body: Annotated[OutboundEventRequest, Body(openapi_examples=OUTBOUND_EVENT_EXAMPLES)],
    session: DbSession,
    now: NowTimestamp,
    _principal: Annotated[AdminPrincipal, Depends(RequireAdmin)],
) -> OutboundEventAcceptedResponse | JSONResponse:
    correlation_id = (
        str(body.correlation_id) if body.correlation_id is not None else get_correlation_id()
    )
    if correlation_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Correlation context missing.",
        )

    result = await enqueue_outbound_for_event(
        session,
        partner_id=body.partner_id,
        event_type=body.event_type,
        payload=body.payload,
        idempotency_key=body.idempotency_key,
        correlation_id=correlation_id,
        now=utc_now_from_timestamp(now),
    )
    if isinstance(result, OutboundEventDuplicateResponse):
        return _duplicate_json(result)
    return result

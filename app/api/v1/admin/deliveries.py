"""Admin delivery list, detail, attempts, and replay (spec §7.1.3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import AdminPrincipal, RequireAdmin, RequireOperator, RequireViewer
from app.api.deps import DbSession, NowTimestamp, Pagination, RedisClient
from app.api.v1.admin.mappers import attempt_to_response, delivery_to_response
from app.config import Settings, get_settings
from app.domain.enums import DeliveryStatus, ReplayApprovalStatus
from app.domain.models.attempt import DeliveryAttempt
from app.domain.models.delivery import Delivery
from app.domain.models.partner import Partner
from app.domain.services.delivery_service import utc_now_from_timestamp
from app.domain.services.outbound_enqueue import enqueue_outbound_for_event
from app.domain.services.replay_approval_service import create_replay_approval
from app.domain.services.replay_service import (
    bulk_replay_deliveries,
    fetch_delivery_with_partner,
    replay_delivery,
)
from app.logging import get_correlation_id
from app.schemas.common import AdminErrorResponse
from app.schemas.delivery import (
    BULK_REPLAY_REQUEST_EXAMPLES,
    REPLAY_REQUEST_EXAMPLES,
    BulkReplayRequest,
    BulkReplayResponse,
    DeliveryReplayRequest,
    DeliveryReplayResponse,
    DeliveryResponse,
    PaginatedAttemptsResponse,
    PaginatedDeliveriesResponse,
)
from app.schemas.outbound import (
    OutboundEventAcceptedResponse,
    OutboundEventDuplicateResponse,
)
from app.schemas.replay_approval import ReplayApprovalPendingResponse
from app.schemas.sandbox import SANDBOX_TEST_EXAMPLES, SandboxTestDeliveryRequest

router = APIRouter(prefix="/admin/v1/deliveries", tags=["admin"])

_ADMIN_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": AdminErrorResponse, "description": "Missing or invalid admin credentials."},
    403: {"model": AdminErrorResponse, "description": "Insufficient role for this operation."},
    404: {"model": AdminErrorResponse, "description": "Delivery not found."},
    409: {"model": AdminErrorResponse, "description": "Delivery status does not allow replay."},
    422: {"model": AdminErrorResponse, "description": "Validation error."},
}

_REPLAY_ERRORS: dict[int | str, dict[str, object]] = {
    **_ADMIN_ERRORS,
    202: {
        "model": ReplayApprovalPendingResponse,
        "description": "Replay queued for admin approval.",
    },
}


def _apply_delivery_filters(
    stmt: Select[tuple[Delivery, Partner]],
    *,
    partner_id: uuid.UUID | None,
    status_filter: DeliveryStatus | None,
    event_type: str | None,
    from_dt: datetime | None,
    to_dt: datetime | None,
    correlation_id: str | None,
    sla_breached: bool | None,
) -> Select[tuple[Delivery, Partner]]:
    if partner_id is not None:
        stmt = stmt.where(Partner.public_id == partner_id)
    if status_filter is not None:
        stmt = stmt.where(Delivery.status == status_filter.value)
    if event_type is not None:
        stmt = stmt.where(Delivery.event_type == event_type)
    if from_dt is not None:
        stmt = stmt.where(Delivery.created_at >= from_dt)
    if to_dt is not None:
        stmt = stmt.where(Delivery.created_at <= to_dt)
    if correlation_id is not None:
        stmt = stmt.where(Delivery.correlation_id == correlation_id)
    if sla_breached is not None:
        stmt = stmt.where(Delivery.sla_breached == sla_breached)
    return stmt


async def _get_delivery_partner_or_404(
    session: AsyncSession,
    delivery_id: uuid.UUID,
) -> tuple[Delivery, Partner]:
    row = await fetch_delivery_with_partner(session, delivery_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found.")
    return row


@router.get(
    "",
    response_model=PaginatedDeliveriesResponse,
    responses=_ADMIN_ERRORS,
    summary="List deliveries",
    description=(
        "Paginated delivery list. Path and `partner_id` filters use public UUIDv7 identifiers. "
        "Payload may be present on detail views only according to the response model."
    ),
)
async def list_deliveries(
    session: DbSession,
    pagination: Pagination,
    _principal: Annotated[AdminPrincipal, Depends(RequireViewer)],
    partner_id: Annotated[
        uuid.UUID | None,
        Query(description="Filter by partner public UUIDv7 identifier."),
    ] = None,
    status_filter: Annotated[
        DeliveryStatus | None,
        Query(alias="status", description="Filter by delivery status."),
    ] = None,
    event_type: Annotated[str | None, Query(description="Filter by event type.")] = None,
    from_dt: Annotated[
        datetime | None,
        Query(alias="from", description="Inclusive lower bound on created_at (ISO 8601)."),
    ] = None,
    to_dt: Annotated[
        datetime | None,
        Query(alias="to", description="Inclusive upper bound on created_at (ISO 8601)."),
    ] = None,
    correlation_id: Annotated[
        str | None,
        Query(description="Filter by correlation identifier."),
    ] = None,
    sla_breached: Annotated[
        bool | None,
        Query(description="Filter by SLA breach flag."),
    ] = None,
) -> PaginatedDeliveriesResponse:
    base = select(Delivery, Partner).join(Partner, Delivery.partner_id == Partner.id)
    filtered = _apply_delivery_filters(
        base,
        partner_id=partner_id,
        status_filter=status_filter,
        event_type=event_type,
        from_dt=from_dt,
        to_dt=to_dt,
        correlation_id=correlation_id,
        sla_breached=sla_breached,
    )
    count_stmt = select(func.count()).select_from(filtered.subquery())
    total = int((await session.execute(count_stmt)).scalar_one())
    page_stmt = (
        filtered.order_by(Delivery.created_at.desc())
        .limit(pagination.limit)
        .offset(pagination.offset)
    )
    rows = (await session.execute(page_stmt)).all()
    items = [
        delivery_to_response(delivery, partner_public_id=partner.public_id)
        for delivery, partner in rows
    ]
    return PaginatedDeliveriesResponse(
        items=items,
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post(
    "/bulk-replay",
    response_model=BulkReplayResponse,
    responses=_ADMIN_ERRORS,
    summary="Bulk replay failed deliveries",
    description=(
        "Admin-only replay of up to 100 failed deliveries. `reason` is required and audited. "
        "Open circuits and rate limits skip individual ids instead of failing the batch."
    ),
)
async def bulk_replay_deliveries_endpoint(
    body: Annotated[BulkReplayRequest, Body(openapi_examples=BULK_REPLAY_REQUEST_EXAMPLES)],
    session: DbSession,
    redis: RedisClient,
    settings: Annotated[Settings, Depends(get_settings)],
    principal: Annotated[AdminPrincipal, Depends(RequireAdmin)],
) -> BulkReplayResponse:
    result = await bulk_replay_deliveries(
        session,
        delivery_public_ids=body.delivery_ids,
        actor_id=principal.sub,
        reason=body.reason,
        redis=redis,
        settings=settings,
    )
    return BulkReplayResponse(
        requested=result.requested,
        replayed=result.replayed,
        skipped_open_circuit=result.skipped_open_circuit,
        skipped_rate_limited=result.skipped_rate_limited,
        skipped_invalid_status=result.skipped_invalid_status,
        not_found=result.not_found,
    )


_SANDBOX_ERRORS: dict[int | str, dict[str, object]] = {
    **_ADMIN_ERRORS,
    200: {
        "model": OutboundEventDuplicateResponse,
        "description": "Duplicate idempotency_key; same delivery_ids returned without republish.",
    },
}


@router.post(
    "/test",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=OutboundEventAcceptedResponse,
    responses=_SANDBOX_ERRORS,
    summary="Enqueue sandbox test delivery",
    description=(
        "Admin-only test send against a partner endpoint (typically partner-mock). "
        "Uses the same persist path as internal outbound; `partner_id` is a public UUIDv7."
    ),
)
async def sandbox_test_delivery(
    body: Annotated[SandboxTestDeliveryRequest, Body(openapi_examples=SANDBOX_TEST_EXAMPLES)],
    session: DbSession,
    now: NowTimestamp,
    _principal: Annotated[AdminPrincipal, Depends(RequireAdmin)],
) -> OutboundEventAcceptedResponse | JSONResponse:
    idempotency_key = body.idempotency_key or f"sandbox::{uuid.uuid4()}"
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
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        now=utc_now_from_timestamp(now),
    )
    if isinstance(result, OutboundEventDuplicateResponse):
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=result.model_dump(mode="json"),
        )
    return result


@router.get(
    "/{id}",
    response_model=DeliveryResponse,
    responses=_ADMIN_ERRORS,
    summary="Get delivery with attempts",
    description=(
        "Delivery detail by public UUIDv7, including nested attempts. "
        "Replay does not mutate the stored payload."
    ),
)
async def get_delivery(
    id: Annotated[uuid.UUID, Path(description="Delivery public UUIDv7 identifier.")],
    session: DbSession,
    _principal: Annotated[AdminPrincipal, Depends(RequireViewer)],
) -> DeliveryResponse:
    delivery, partner = await _get_delivery_partner_or_404(session, id)
    attempts_result = await session.execute(
        select(DeliveryAttempt)
        .where(DeliveryAttempt.delivery_id == delivery.id)
        .order_by(DeliveryAttempt.attempt_number.asc())
    )
    attempts = list(attempts_result.scalars().all())
    return delivery_to_response(
        delivery,
        partner_public_id=partner.public_id,
        attempts=[attempt_to_response(a) for a in attempts],
    )


@router.get(
    "/{id}/attempts",
    response_model=PaginatedAttemptsResponse,
    responses=_ADMIN_ERRORS,
    summary="List delivery attempts",
    description="Paginated HTTP attempt history for a delivery public UUIDv7.",
)
async def list_delivery_attempts(
    id: Annotated[uuid.UUID, Path(description="Delivery public UUIDv7 identifier.")],
    session: DbSession,
    pagination: Pagination,
    _principal: Annotated[AdminPrincipal, Depends(RequireViewer)],
) -> PaginatedAttemptsResponse:
    delivery, _partner = await _get_delivery_partner_or_404(session, id)
    count_stmt = (
        select(func.count())
        .select_from(DeliveryAttempt)
        .where(DeliveryAttempt.delivery_id == delivery.id)
    )
    total = int((await session.execute(count_stmt)).scalar_one())
    stmt = (
        select(DeliveryAttempt)
        .where(DeliveryAttempt.delivery_id == delivery.id)
        .order_by(DeliveryAttempt.attempt_number.asc())
        .limit(pagination.limit)
        .offset(pagination.offset)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return PaginatedAttemptsResponse(
        items=[attempt_to_response(a) for a in rows],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post(
    "/{id}/replay",
    response_model=DeliveryReplayResponse,
    responses=_REPLAY_ERRORS,
    summary="Replay a failed delivery",
    description=(
        "`reason` is required and written to the audit log. When replay approval is enabled, "
        "returns **202** with a pending approval id and does not re-queue yet. Otherwise "
        "transitions a **failed** delivery to replaying (**409** if the status is not failed)."
    ),
)
async def replay_delivery_endpoint(
    id: Annotated[uuid.UUID, Path(description="Delivery public UUIDv7 identifier.")],
    body: Annotated[DeliveryReplayRequest, Body(openapi_examples=REPLAY_REQUEST_EXAMPLES)],
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
    principal: Annotated[AdminPrincipal, Depends(RequireOperator)],
) -> DeliveryReplayResponse | JSONResponse:
    if settings.hub_replay_approval_required:
        approval = await create_replay_approval(
            session,
            delivery_public_id=id,
            actor_id=principal.sub,
            reason=body.reason,
        )
        payload = ReplayApprovalPendingResponse(
            approval_id=approval.id,
            status=ReplayApprovalStatus(approval.status),
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=payload.model_dump(mode="json"),
        )

    delivery = await replay_delivery(
        session,
        delivery_public_id=id,
        actor_id=principal.sub,
        reason=body.reason,
        reset_attempt_counter=body.reset_attempt_counter,
    )
    return DeliveryReplayResponse(
        delivery_id=delivery.public_id,
        status=DeliveryStatus(delivery.status),
    )

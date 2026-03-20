"""Admin dead-letter queue list, acknowledge, and purge (spec §7.1.3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import AdminPrincipal, RequireAdmin, RequireOperator, RequireViewer
from app.api.deps import DbSession, Pagination
from app.api.v1.admin.mappers import dead_letter_to_response
from app.domain.enums import DeadLetterReason
from app.domain.models.audit import AuditLog
from app.domain.models.dead_letter import DeadLetter
from app.domain.models.delivery import Delivery
from app.domain.models.partner import Partner
from app.schemas.common import AdminErrorResponse
from app.schemas.delivery import (
    DEAD_LETTER_PURGE_EXAMPLES,
    DeadLetterAckResponse,
    DeadLetterPurgeRequest,
    DeadLetterPurgeResponse,
    PaginatedDeadLettersResponse,
)

router = APIRouter(prefix="/admin/v1/dead-letters", tags=["admin"])

_ADMIN_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": AdminErrorResponse, "description": "Missing or invalid admin credentials."},
    403: {"model": AdminErrorResponse, "description": "Insufficient role for this operation."},
    404: {"model": AdminErrorResponse, "description": "Dead-letter entry not found."},
    409: {"model": AdminErrorResponse, "description": "Dead-letter entry already acknowledged."},
    422: {"model": AdminErrorResponse, "description": "Validation error."},
}


async def _get_dead_letter_row_or_404(
    session: AsyncSession,
    dead_letter_id: uuid.UUID,
) -> tuple[DeadLetter, Delivery, Partner]:
    result = await session.execute(
        select(DeadLetter, Delivery, Partner)
        .join(Delivery, DeadLetter.delivery_id == Delivery.id)
        .join(Partner, DeadLetter.partner_id == Partner.id)
        .where(DeadLetter.id == dead_letter_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dead-letter entry not found.",
        )
    dead_letter, delivery, partner = row
    return dead_letter, delivery, partner


@router.get(
    "",
    response_model=PaginatedDeadLettersResponse,
    responses=_ADMIN_ERRORS,
    summary="List dead-letter queue entries",
    description=(
        "Paginated DLQ. Identifiers are public UUIDv7 values. "
        "Manual purge tombstones use reason `manual_purge`."
    ),
)
async def list_dead_letters(
    session: DbSession,
    pagination: Pagination,
    _principal: Annotated[AdminPrincipal, Depends(RequireViewer)],
) -> PaginatedDeadLettersResponse:
    base = (
        select(DeadLetter, Delivery, Partner)
        .join(Delivery, DeadLetter.delivery_id == Delivery.id)
        .join(Partner, DeadLetter.partner_id == Partner.id)
    )
    count_stmt = select(func.count()).select_from(base.subquery())
    total = int((await session.execute(count_stmt)).scalar_one())
    stmt = (
        base.order_by(DeadLetter.created_at.desc())
        .limit(pagination.limit)
        .offset(pagination.offset)
    )
    rows = (await session.execute(stmt)).all()
    items = [
        dead_letter_to_response(
            dead_letter,
            delivery_public_id=delivery.public_id,
            partner_public_id=partner.public_id,
        )
        for dead_letter, delivery, partner in rows
    ]
    return PaginatedDeadLettersResponse(
        items=items,
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post(
    "/{id}/ack",
    response_model=DeadLetterAckResponse,
    responses=_ADMIN_ERRORS,
    summary="Acknowledge a dead-letter entry",
    description=(
        "Marks a DLQ row acknowledged (operator). Already-acknowledged rows return **409**. "
        "Does not replay the delivery."
    ),
)
async def acknowledge_dead_letter(
    id: Annotated[uuid.UUID, Path(description="Dead-letter public UUIDv7 identifier.")],
    session: DbSession,
    principal: Annotated[AdminPrincipal, Depends(RequireOperator)],
) -> DeadLetterAckResponse:
    dead_letter, delivery, _partner = await _get_dead_letter_row_or_404(session, id)
    if dead_letter.acknowledged_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dead-letter entry is already acknowledged.",
        )

    now = datetime.now(UTC)
    dead_letter.acknowledged_at = now
    dead_letter.acknowledged_by = principal.sub

    audit = AuditLog(
        actor_id=principal.sub,
        action="dlq.ack",
        resource_type="dead_letter",
        resource_id=dead_letter.id,
        metadata_={"delivery_id": str(delivery.public_id)},
    )
    session.add(audit)
    await session.commit()

    return DeadLetterAckResponse(
        id=dead_letter.id,
        acknowledged_at=now,
        acknowledged_by=principal.sub,
    )


@router.delete(
    "/{id}",
    response_model=DeadLetterPurgeResponse,
    responses=_ADMIN_ERRORS,
    summary="Purge (tombstone) a dead-letter entry",
    description=(
        "Admin-only tombstone. `reason` is required and audited. "
        "Sets the DLQ reason to `manual_purge`."
    ),
)
async def purge_dead_letter(
    id: Annotated[uuid.UUID, Path(description="Dead-letter public UUIDv7 identifier.")],
    body: Annotated[DeadLetterPurgeRequest, Body(openapi_examples=DEAD_LETTER_PURGE_EXAMPLES)],
    session: DbSession,
    principal: Annotated[AdminPrincipal, Depends(RequireAdmin)],
) -> DeadLetterPurgeResponse:
    dead_letter, _delivery, _partner = await _get_dead_letter_row_or_404(session, id)

    dead_letter.reason = DeadLetterReason.MANUAL_PURGE.value

    audit = AuditLog(
        actor_id=principal.sub,
        action="dlq.purge",
        resource_type="dead_letter",
        resource_id=dead_letter.id,
        metadata_={"reason": body.reason},
    )
    session.add(audit)
    await session.commit()

    return DeadLetterPurgeResponse(
        id=dead_letter.id,
        reason=dead_letter.reason,
    )

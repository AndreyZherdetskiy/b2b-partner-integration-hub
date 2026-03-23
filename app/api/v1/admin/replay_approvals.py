"""Admin replay approval approve and reject (spec §3.5)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy import func, select

from app.api.auth import AdminPrincipal, RequireAdmin, RequireViewer
from app.api.deps import DbSession, Pagination
from app.domain.enums import DeliveryStatus, ReplayApprovalStatus
from app.domain.models.delivery import Delivery
from app.domain.models.partner import Partner
from app.domain.models.replay_approval import ReplayApproval
from app.domain.services.replay_approval_service import (
    approve_replay_approval,
    reject_replay_approval,
)
from app.schemas.common import AdminErrorResponse
from app.schemas.replay_approval import (
    REPLAY_REJECT_EXAMPLES,
    PaginatedReplayApprovalsResponse,
    ReplayApprovalActionResponse,
    ReplayApprovalListItem,
    ReplayApprovalRejectedResponse,
    ReplayApprovalRejectRequest,
)

router = APIRouter(prefix="/admin/v1/replay-approvals", tags=["admin"])

_ADMIN_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": AdminErrorResponse, "description": "Missing or invalid admin credentials."},
    403: {"model": AdminErrorResponse, "description": "Insufficient role for this operation."},
    404: {"model": AdminErrorResponse, "description": "Replay approval not found."},
    409: {"model": AdminErrorResponse, "description": "Replay approval is not pending."},
    422: {"model": AdminErrorResponse, "description": "Validation error."},
}


@router.get(
    "",
    response_model=PaginatedReplayApprovalsResponse,
    responses=_ADMIN_ERRORS,
    summary="List replay approval requests",
    description=(
        "Paginated approval queue. Defaults to `pending`. "
        "Delivery ids in the payload are public UUIDv7 values."
    ),
)
async def list_replay_approvals(
    session: DbSession,
    pagination: Pagination,
    _principal: Annotated[AdminPrincipal, Depends(RequireViewer)],
    status_filter: Annotated[
        ReplayApprovalStatus | None,
        Query(
            alias="status",
            description="Filter by approval status; defaults to pending.",
        ),
    ] = None,
) -> PaginatedReplayApprovalsResponse:
    effective_status = status_filter or ReplayApprovalStatus.PENDING
    base = (
        select(ReplayApproval, Delivery, Partner)
        .join(Delivery, ReplayApproval.delivery_id == Delivery.id)
        .join(Partner, Delivery.partner_id == Partner.id)
        .where(ReplayApproval.status == effective_status.value)
    )
    count_stmt = select(func.count()).select_from(base.subquery())
    total = int((await session.execute(count_stmt)).scalar_one())
    page_stmt = (
        base.order_by(ReplayApproval.created_at.desc())
        .limit(pagination.limit)
        .offset(pagination.offset)
    )
    rows = (await session.execute(page_stmt)).all()
    items = [
        ReplayApprovalListItem(
            id=approval.id,
            delivery_id=delivery.public_id,
            reason=approval.reason,
            requested_by=approval.requested_by,
            approved_by=approval.approved_by,
            status=ReplayApprovalStatus(approval.status),
            created_at=approval.created_at,
        )
        for approval, delivery, _partner in rows
    ]
    return PaginatedReplayApprovalsResponse(
        items=items,
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post(
    "/{id}/approve",
    response_model=ReplayApprovalActionResponse,
    responses=_ADMIN_ERRORS,
    summary="Approve a pending replay request",
    description=(
        "Admin-only. Re-queues the failed delivery using the original payload. "
        "Non-pending rows return **409**."
    ),
)
async def approve_replay_approval_endpoint(
    id: Annotated[uuid.UUID, Path(description="Replay approval public UUIDv7 identifier.")],
    session: DbSession,
    principal: Annotated[AdminPrincipal, Depends(RequireAdmin)],
) -> ReplayApprovalActionResponse:
    delivery = await approve_replay_approval(
        session,
        approval_id=id,
        actor_id=principal.sub,
    )
    return ReplayApprovalActionResponse(
        delivery_id=delivery.public_id,
        status=DeliveryStatus(delivery.status),
    )


@router.post(
    "/{id}/reject",
    response_model=ReplayApprovalRejectedResponse,
    responses=_ADMIN_ERRORS,
    summary="Reject a pending replay request",
    description=("Admin-only. `reason` is required and audited. Non-pending rows return **409**."),
)
async def reject_replay_approval_endpoint(
    id: Annotated[uuid.UUID, Path(description="Replay approval public UUIDv7 identifier.")],
    body: Annotated[ReplayApprovalRejectRequest, Body(openapi_examples=REPLAY_REJECT_EXAMPLES)],
    session: DbSession,
    principal: Annotated[AdminPrincipal, Depends(RequireAdmin)],
) -> ReplayApprovalRejectedResponse:
    approval = await reject_replay_approval(
        session,
        approval_id=id,
        actor_id=principal.sub,
        reason=body.reason,
    )
    return ReplayApprovalRejectedResponse(
        approval_id=approval.id,
        status=ReplayApprovalStatus(approval.status),
    )

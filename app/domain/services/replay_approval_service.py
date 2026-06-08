"""Replay approval workflow (operator request → admin confirm)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import DeliveryStatus, ReplayApprovalStatus
from app.domain.errors import (
    DeliveryNotFoundError,
    DeliveryNotReplayableError,
    ReplayApprovalNotFoundError,
    ReplayApprovalNotPendingError,
)
from app.domain.ids import generate_uuidv7
from app.domain.models.audit import AuditLog
from app.domain.models.delivery import Delivery
from app.domain.models.partner import Partner
from app.domain.models.replay_approval import ReplayApproval
from app.domain.services.replay_service import fetch_delivery_with_partner, replay_delivery


async def fetch_replay_approval_with_delivery(
    session: AsyncSession,
    approval_id: uuid.UUID,
) -> tuple[ReplayApproval, Delivery, Partner] | None:
    result = await session.execute(
        select(ReplayApproval, Delivery, Partner)
        .join(Delivery, ReplayApproval.delivery_id == Delivery.id)
        .join(Partner, Delivery.partner_id == Partner.id)
        .where(ReplayApproval.id == approval_id)
    )
    row = result.one_or_none()
    if row is None:
        return None
    approval, delivery, partner = row
    return approval, delivery, partner


async def create_replay_approval(
    session: AsyncSession,
    *,
    delivery_public_id: uuid.UUID,
    actor_id: str,
    reason: str,
) -> ReplayApproval:
    row = await fetch_delivery_with_partner(session, delivery_public_id)
    if row is None:
        raise DeliveryNotFoundError()
    delivery, _partner = row

    current = DeliveryStatus(delivery.status)
    if current is not DeliveryStatus.FAILED:
        raise DeliveryNotReplayableError(current.value)

    approval = ReplayApproval(
        id=generate_uuidv7(),
        delivery_id=delivery.id,
        reason=reason,
        requested_by=actor_id,
        status=ReplayApprovalStatus.PENDING.value,
    )
    session.add(approval)
    await session.commit()
    await session.refresh(approval)
    return approval


async def approve_replay_approval(
    session: AsyncSession,
    *,
    approval_id: uuid.UUID,
    actor_id: str,
) -> Delivery:
    row = await fetch_replay_approval_with_delivery(session, approval_id)
    if row is None:
        raise ReplayApprovalNotFoundError()
    approval, delivery, _partner = row

    if ReplayApprovalStatus(approval.status) is not ReplayApprovalStatus.PENDING:
        raise ReplayApprovalNotPendingError(approval.status)

    replayed = await replay_delivery(
        session,
        delivery_public_id=delivery.public_id,
        actor_id=actor_id,
        reason=approval.reason,
        reset_attempt_counter=False,
    )

    approval.status = ReplayApprovalStatus.APPROVED.value
    approval.approved_by = actor_id

    approve_audit = AuditLog(
        actor_id=actor_id,
        action="replay.approve",
        resource_type="delivery",
        resource_id=delivery.public_id,
        metadata_={"approval_id": str(approval.id), "reason": approval.reason},
    )
    session.add(approve_audit)
    await session.commit()
    await session.refresh(approval)

    return replayed


async def reject_replay_approval(
    session: AsyncSession,
    *,
    approval_id: uuid.UUID,
    actor_id: str,
    reason: str,
) -> ReplayApproval:
    row = await fetch_replay_approval_with_delivery(session, approval_id)
    if row is None:
        raise ReplayApprovalNotFoundError()
    approval, delivery, _partner = row

    if ReplayApprovalStatus(approval.status) is not ReplayApprovalStatus.PENDING:
        raise ReplayApprovalNotPendingError(approval.status)

    approval.status = ReplayApprovalStatus.REJECTED.value
    approval.approved_by = actor_id

    reject_audit = AuditLog(
        actor_id=actor_id,
        action="replay.reject",
        resource_type="delivery",
        resource_id=delivery.public_id,
        metadata_={"approval_id": str(approval.id), "reason": reason},
    )
    session.add(reject_audit)
    await session.commit()
    await session.refresh(approval)

    return approval

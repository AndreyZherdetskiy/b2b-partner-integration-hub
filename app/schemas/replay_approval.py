"""Replay approval request and response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.domain.enums import DeliveryStatus, ReplayApprovalStatus
from app.schemas.common import PaginatedResponse


class ReplayApprovalPendingResponse(BaseModel):
    """Replay request queued for admin approval."""

    approval_id: uuid.UUID = Field(
        ...,
        description="Replay approval public UUIDv7 identifier.",
    )
    status: ReplayApprovalStatus = Field(
        ...,
        description="Approval workflow status (pending until admin acts).",
    )


REPLAY_REJECT_EXAMPLES = {
    "duplicate": {
        "summary": "Reject a duplicate replay request",
        "value": {"reason": "Duplicate replay request; partner already recovered."},
    },
}


class ReplayApprovalRejectRequest(BaseModel):
    """Body for POST /admin/v1/replay-approvals/{id}/reject."""

    reason: str = Field(
        ...,
        description="Required admin reason for rejection audit trail (non-empty).",
        examples=["Duplicate replay request; partner already recovered."],
    )

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "reason must not be empty"
            raise ValueError(msg)
        return stripped


class ReplayApprovalActionResponse(BaseModel):
    """Delivery state after an approved replay."""

    delivery_id: uuid.UUID = Field(..., description="Delivery public UUIDv7 identifier.")
    status: DeliveryStatus = Field(
        ...,
        description="Delivery status after replay approval (replaying).",
    )


class ReplayApprovalRejectedResponse(BaseModel):
    """Replay approval rejection acknowledgement."""

    approval_id: uuid.UUID = Field(
        ...,
        description="Replay approval public UUIDv7 identifier.",
    )
    status: ReplayApprovalStatus = Field(
        ...,
        description="Approval workflow status after rejection.",
    )


class ReplayApprovalListItem(BaseModel):
    """Single replay approval row for the admin queue."""

    id: uuid.UUID = Field(..., description="Replay approval public UUIDv7 identifier.")
    delivery_id: uuid.UUID = Field(..., description="Delivery public UUIDv7 identifier.")
    reason: str = Field(..., description="Operator reason for the replay request.")
    requested_by: str = Field(..., description="Operator who requested the replay.")
    approved_by: str | None = Field(
        default=None,
        description="Admin who approved or rejected; null while pending.",
    )
    status: ReplayApprovalStatus = Field(..., description="Approval workflow status.")
    created_at: datetime = Field(..., description="When the approval request was created.")


class PaginatedReplayApprovalsResponse(PaginatedResponse[ReplayApprovalListItem]):
    """Paginated replay approval queue."""

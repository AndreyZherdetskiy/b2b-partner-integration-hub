"""Internal outbound event request/response schemas (spec §7.1.5, §7.3)."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

OUTBOUND_EVENT_EXAMPLES: dict[str, dict[str, object]] = {
    "order_created": {
        "summary": "Enqueue order.created for partner delivery",
        "value": {
            "partner_id": "0194a2b3-c4d5-7890-abcd-ef1234567890",
            "event_type": "order.created",
            "payload": {"order_id": "ord_123", "amount": 99.99},
            "idempotency_key": "idem-order-created-1",
            "correlation_id": "0194a2b3-c4d5-7890-abcd-ef1234567891",
        },
    },
}


class OutboundEventRequest(BaseModel):
    """Body for internal outbound delivery creation."""

    partner_id: UUID = Field(..., description="Partner public UUIDv7 identifier.")
    event_type: str = Field(..., description="Event type to deliver (e.g. order.created).")
    payload: dict[str, Any] = Field(
        ...,
        description="Opaque event payload for the partner webhook.",
    )
    idempotency_key: str = Field(
        ...,
        min_length=1,
        description=(
            "Caller-supplied idempotency key; duplicates return the same delivery_ids "
            "(one delivery per matching endpoint)."
        ),
    )
    correlation_id: UUID | None = Field(
        default=None,
        description="Optional UUIDv7 correlation id; defaults to X-Correlation-Id when omitted.",
    )

    @field_validator("correlation_id")
    @classmethod
    def correlation_id_must_be_uuidv7(cls, value: UUID | None) -> UUID | None:
        if value is not None and value.version != 7:
            msg = "correlation_id must be a UUID version 7"
            raise ValueError(msg)
        return value


class OutboundEventAcceptedResponse(BaseModel):
    """202 response when a new delivery is accepted."""

    delivery_id: UUID = Field(
        ...,
        description="First delivery public UUIDv7 identifier (backward compatibility).",
    )
    delivery_ids: list[UUID] = Field(
        ...,
        description="All delivery public UUIDv7 identifiers created for matching endpoints.",
    )
    status: Literal["accepted"] = Field(
        default="accepted",
        description="Processing status for a newly accepted delivery.",
    )


class OutboundEventDuplicateResponse(BaseModel):
    """200 response when idempotency_key was already processed."""

    delivery_id: UUID = Field(
        ...,
        description="First delivery public UUIDv7 identifier (backward compatibility).",
    )
    delivery_ids: list[UUID] = Field(
        ...,
        description="All delivery public UUIDv7 identifiers for the caller idempotency key.",
    )
    status: Literal["duplicate"] = Field(
        default="duplicate",
        description="Indicates the idempotency key was already seen.",
    )


class OutboundErrorResponse(BaseModel):
    """Standard internal outbound API error payload."""

    detail: str = Field(..., description="Human-readable error message.")

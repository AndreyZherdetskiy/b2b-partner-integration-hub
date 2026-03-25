"""Inbound webhook request/response schemas (spec §7.1.1)."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

INBOUND_EVENT_EXAMPLES: dict[str, dict[str, object]] = {
    "happy": {
        "summary": "Accepted order.created webhook",
        "value": {
            "event_type": "order.created",
            "payload": {"order_id": "ord_123", "amount": 99.99},
        },
    },
    "bad_timestamp": {
        "summary": "Valid body; timestamp skew rejected with 403",
        "description": "Use X-Hub-Timestamp more than 300 seconds from server time.",
        "value": {
            "event_type": "order.created",
            "payload": {"order_id": "ord_456"},
        },
    },
}


class InboundEventBody(BaseModel):
    """JSON body for inbound webhook POST."""

    event_type: str = Field(..., description="Event type (e.g. order.created).")
    payload: dict[str, Any] = Field(..., description="Opaque event payload from partner.")


class InboundEventAcceptedResponse(BaseModel):
    """202 response when the event is accepted for processing."""

    event_id: UUID = Field(..., description="Inbound event public UUIDv7 identifier.")
    status: Literal["accepted"] = Field(
        default="accepted",
        description="Processing status for a newly accepted event.",
    )


class InboundEventDuplicateResponse(BaseModel):
    """200 response when Idempotency-Key was already processed."""

    event_id: UUID = Field(..., description="Original inbound event UUIDv7 identifier.")
    status: Literal["duplicate"] = Field(
        default="duplicate",
        description="Indicates the idempotency key was already seen.",
    )


class InboundErrorResponse(BaseModel):
    """Standard inbound API error payload."""

    detail: str = Field(..., description="Human-readable error message.")

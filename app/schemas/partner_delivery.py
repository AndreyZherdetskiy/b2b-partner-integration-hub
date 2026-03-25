"""Partner-facing delivery status schemas (read-only, no raw payload)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import DeliveryStatus

PARTNER_DELIVERY_STATUS_EXAMPLES: dict[str, dict[str, object]] = {
    "failed": {
        "summary": "Failed delivery awaiting operator replay",
        "value": {
            "id": "0194a2b3-c4d5-7890-abcd-ef1234567890",
            "status": "failed",
            "event_type": "order.created",
            "attempt_count": 2,
            "last_error_code": "http_400",
            "sla_breached": False,
            "first_success_at": None,
        },
    },
}


class PartnerDeliveryStatusResponse(BaseModel):
    """Read-only delivery status for the authenticated partner."""

    id: uuid.UUID = Field(..., description="Delivery public UUIDv7 identifier.")
    status: DeliveryStatus = Field(..., description="Delivery lifecycle status.")
    event_type: str = Field(..., description="Outbound event type.")
    attempt_count: int = Field(..., description="Number of completed delivery attempts.")
    last_error_code: str | None = Field(
        default=None,
        description="Last terminal or retryable error code.",
    )
    sla_breached: bool = Field(
        ...,
        description="Whether SLA was breached before first success.",
    )
    first_success_at: datetime | None = Field(
        default=None,
        description="Timestamp of first HTTP 2xx; unchanged by replay.",
    )


class PartnerErrorResponse(BaseModel):
    """Partner API error body."""

    detail: str = Field(..., description="Human-readable error message.")

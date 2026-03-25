"""Admin sandbox test delivery schemas."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

SANDBOX_TEST_EXAMPLES: dict[str, dict[str, object]] = {
    "order_created": {
        "summary": "Sandbox order.created for partner delivery",
        "value": {
            "partner_id": "0194a2b3-c4d5-7890-abcd-ef1234567890",
            "event_type": "order.created",
            "payload": {"order_id": "ord_sandbox_1", "amount": 42.0},
        },
    },
}


class SandboxTestDeliveryRequest(BaseModel):
    """Body for POST /admin/v1/deliveries/test."""

    partner_id: UUID = Field(..., description="Partner public UUIDv7 identifier.")
    event_type: str = Field(..., description="Event type to deliver (e.g. order.created).")
    payload: dict[str, Any] = Field(
        ...,
        description="JSON object payload for the partner webhook.",
    )
    idempotency_key: str | None = Field(
        default=None,
        description=(
            "Optional caller idempotency key; when omitted a unique sandbox:: UUID is generated."
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

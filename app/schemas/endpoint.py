"""Partner endpoint admin request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import EndpointDirection, EndpointStatus

ENDPOINT_CREATE_EXAMPLES = {
    "outbound_orders": {
        "summary": "Outbound order events",
        "value": {
            "direction": "outbound",
            "url": "https://partner.example/hooks/orders",
            "event_types": ["order.created", "order.updated"],
        },
    },
}


class EndpointCreate(BaseModel):
    """Body for POST /admin/v1/partners/{id}/endpoints."""

    direction: EndpointDirection = Field(
        ...,
        description="Traffic direction for this endpoint.",
    )
    url: str = Field(
        ...,
        min_length=1,
        description="HTTPS webhook URL (outbound) or registered inbound URL.",
    )
    event_types: list[str] = Field(
        ...,
        min_length=1,
        description="Subscribed event type names.",
        examples=[["order.created"]],
    )
    sla_seconds: int | None = Field(
        default=None,
        ge=1,
        description="Optional per-endpoint SLA override in seconds.",
    )
    max_attempts: int | None = Field(
        default=None,
        ge=1,
        le=255,
        description="Maximum delivery attempts (snapshot at delivery creation).",
    )
    timeout_connect_ms: int | None = Field(
        default=None,
        ge=100,
        description="HTTP connect timeout in milliseconds.",
    )
    timeout_read_ms: int | None = Field(
        default=None,
        ge=100,
        description="HTTP read timeout in milliseconds.",
    )


ENDPOINT_UPDATE_EXAMPLES = {
    "pause": {
        "summary": "Pause outbound delivery",
        "value": {"status": "paused"},
    },
}


class EndpointUpdate(BaseModel):
    """Body for PATCH /admin/v1/endpoints/{id}."""

    status: EndpointStatus | None = Field(
        default=None,
        description="Endpoint status (active, paused, disabled).",
    )
    url: str | None = Field(
        default=None,
        min_length=1,
        description="Updated webhook URL.",
    )
    event_types: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Updated subscribed event types.",
    )
    sla_seconds: int | None = Field(
        default=None,
        ge=1,
        description="Updated SLA override in seconds.",
    )


class EndpointResponse(BaseModel):
    """Endpoint details for admin GET responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(
        ...,
        description="Endpoint public identifier (UUIDv7 primary key).",
    )
    partner_id: uuid.UUID = Field(
        ...,
        description="Owning partner public identifier (UUIDv7).",
    )
    direction: EndpointDirection = Field(..., description="Traffic direction.")
    url: str = Field(..., description="Configured webhook URL.")
    event_types: list[str] = Field(..., description="Subscribed event types.")
    status: EndpointStatus = Field(..., description="Endpoint status.")
    sla_seconds: int | None = Field(
        default=None,
        description="Per-endpoint SLA override if set.",
    )
    max_attempts: int = Field(..., description="Maximum delivery attempts.")
    timeout_connect_ms: int = Field(..., description="Connect timeout in milliseconds.")
    timeout_read_ms: int = Field(..., description="Read timeout in milliseconds.")
    created_at: datetime = Field(..., description="Creation timestamp (UTC).")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC).")

"""Admin payload schema registry request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import PayloadSchemaStatus

SCHEMA_CREATE_EXAMPLES = {
    "order_created_v1": {
        "summary": "order.created requires order_id",
        "value": {
            "event_type": "order.created",
            "version": 1,
            "json_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    },
}


class PayloadSchemaCreate(BaseModel):
    """Body for POST /admin/v1/schemas."""

    event_type: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Event type name this schema applies to.",
        examples=["order.created"],
    )
    version: int = Field(
        ...,
        ge=1,
        description="Monotonic schema version for the event type.",
    )
    json_schema: dict[str, Any] = Field(
        ...,
        description="JSON Schema document (Draft 2020-12).",
    )


class PayloadSchemaResponse(BaseModel):
    """Registered payload schema row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Schema registry UUIDv7 identifier.")
    event_type: str = Field(..., description="Event type name.")
    version: int = Field(..., description="Schema version number.")
    json_schema: dict[str, Any] = Field(..., description="JSON Schema document.")
    status: PayloadSchemaStatus = Field(..., description="active or deprecated.")
    created_at: datetime = Field(..., description="Row creation timestamp.")
    updated_at: datetime = Field(..., description="Last update timestamp.")


class PayloadSchemaListResponse(BaseModel):
    """List of payload schemas for an event type."""

    items: list[PayloadSchemaResponse] = Field(..., description="Matching schema rows.")
    total: int = Field(..., description="Total number of matching rows.")

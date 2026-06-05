"""Admin delivery, attempt, replay, and dead-letter schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import DeliveryStatus
from app.schemas.common import PaginatedResponse

REPLAY_REQUEST_EXAMPLES = {
    "manual_recovery": {
        "summary": "Replay after partner outage",
        "description": "Operator replay with audit reason; optional attempt counter reset.",
        "value": {
            "reason": "Partner endpoint restored after maintenance window.",
            "reset_attempt_counter": False,
        },
    },
}


class DeliveryAttemptResponse(BaseModel):
    """Single HTTP delivery attempt."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Attempt public UUIDv7 identifier.")
    attempt_number: int = Field(..., description="1-based attempt sequence number.")
    requested_at: datetime = Field(..., description="When the HTTP request was sent.")
    responded_at: datetime | None = Field(
        default=None,
        description="When the HTTP response was received, if any.",
    )
    http_status_code: int | None = Field(
        default=None,
        description="Partner HTTP status code when a response was received.",
    )
    error_type: str | None = Field(
        default=None,
        description="Transport or client error classification when no HTTP status.",
    )
    duration_ms: int | None = Field(
        default=None,
        description="Round-trip duration in milliseconds.",
    )
    created_at: datetime = Field(..., description="Row creation timestamp.")


class DeliveryResponse(BaseModel):
    """Delivery summary for list and detail views."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Delivery public UUIDv7 identifier.")
    partner_id: uuid.UUID = Field(..., description="Partner public UUIDv7 identifier.")
    endpoint_id: uuid.UUID = Field(..., description="Partner endpoint UUID.")
    event_type: str = Field(..., description="Outbound event type.")
    status: DeliveryStatus = Field(..., description="Delivery lifecycle status.")
    attempt_count: int = Field(..., description="Number of completed delivery attempts.")
    max_attempts: int = Field(..., description="Maximum attempts before terminal failure.")
    idempotency_key: str = Field(
        ...,
        description="Partner deduplication key (unchanged on replay).",
    )
    payload: dict[str, object] = Field(
        ...,
        description="Webhook JSON payload (immutable on replay).",
    )
    correlation_id: str = Field(..., description="End-to-end correlation identifier.")
    sla_deadline_at: datetime = Field(..., description="SLA deadline for first success.")
    sla_breached: bool = Field(..., description="Whether SLA was breached before first success.")
    first_success_at: datetime | None = Field(
        default=None,
        description="Timestamp of first HTTP 2xx; unchanged by replay.",
    )
    last_error_code: str | None = Field(
        default=None,
        description="Last terminal or retryable error code.",
    )
    last_error_message: str | None = Field(
        default=None,
        description="Last error message from partner or transport.",
    )
    created_at: datetime = Field(..., description="Delivery creation timestamp.")
    updated_at: datetime = Field(..., description="Last update timestamp.")
    attempts: list[DeliveryAttemptResponse] = Field(
        default_factory=list,
        description="Nested delivery attempts (detail view only).",
    )


class PaginatedDeliveriesResponse(PaginatedResponse[DeliveryResponse]):
    """Paginated delivery list."""


class PaginatedAttemptsResponse(PaginatedResponse[DeliveryAttemptResponse]):
    """Paginated delivery attempts."""


class DeliveryReplayRequest(BaseModel):
    """Body for POST /admin/v1/deliveries/{id}/replay."""

    reason: str = Field(
        ...,
        description="Required operator reason for audit trail (non-empty).",
        examples=["Partner endpoint restored after maintenance."],
    )
    reset_attempt_counter: bool = Field(
        default=False,
        description="When true, resets attempt_count to zero before re-queueing.",
    )

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "reason must not be empty"
            raise ValueError(msg)
        return stripped


class DeliveryReplayResponse(BaseModel):
    """Replay acceptance response."""

    delivery_id: uuid.UUID = Field(..., description="Delivery public UUIDv7 identifier.")
    status: DeliveryStatus = Field(
        ...,
        description="Delivery status after replay request (replaying).",
    )


class DeadLetterResponse(BaseModel):
    """Dead-letter queue row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Dead-letter public UUIDv7 identifier.")
    delivery_id: uuid.UUID = Field(..., description="Related delivery public UUIDv7 identifier.")
    partner_id: uuid.UUID = Field(..., description="Partner public UUIDv7 identifier.")
    reason: str = Field(..., description="Terminal failure reason code.")
    last_http_status: int | None = Field(
        default=None,
        description="Last HTTP status from partner when applicable.",
    )
    last_error_message: str = Field(..., description="Last error message captured at DLQ time.")
    acknowledged_at: datetime | None = Field(
        default=None,
        description="When an operator acknowledged this DLQ entry.",
    )
    acknowledged_by: str | None = Field(
        default=None,
        description="Operator id who acknowledged this DLQ entry.",
    )
    created_at: datetime = Field(..., description="DLQ row creation timestamp.")


class PaginatedDeadLettersResponse(PaginatedResponse[DeadLetterResponse]):
    """Paginated dead-letter list."""


BULK_REPLAY_REQUEST_EXAMPLES = {
    "incident_recovery": {
        "summary": "Bulk replay after partner outage",
        "description": (
            "Admin-only bulk replay with audit reason; respects circuit breaker and rate limits."
        ),
        "value": {
            "delivery_ids": ["0194a2b3-c4d5-7890-abcd-ef1234567890"],
            "reason": "Partner endpoint restored after regional outage.",
        },
    },
}


class BulkReplayRequest(BaseModel):
    """Body for POST /admin/v1/deliveries/bulk-replay."""

    delivery_ids: list[uuid.UUID] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Delivery public UUIDv7 identifiers to replay (1–100, deduplicated).",
    )
    reason: str = Field(
        ...,
        description="Required admin reason for audit trail (non-empty).",
        examples=["Partner endpoint restored after maintenance."],
    )

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "reason must not be empty"
            raise ValueError(msg)
        return stripped

    @field_validator("delivery_ids")
    @classmethod
    def dedupe_delivery_ids(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        seen: set[uuid.UUID] = set()
        deduped: list[uuid.UUID] = []
        for delivery_id in value:
            if delivery_id not in seen:
                seen.add(delivery_id)
                deduped.append(delivery_id)
        return deduped


class BulkReplayResponse(BaseModel):
    """Bulk replay outcome with per-category UUID lists."""

    requested: list[uuid.UUID] = Field(
        ...,
        description="Delivery public IDs submitted after deduplication (input order preserved).",
    )
    replayed: list[uuid.UUID] = Field(
        default_factory=list,
        description="Deliveries successfully enqueued for replay.",
    )
    skipped_open_circuit: list[uuid.UUID] = Field(
        default_factory=list,
        description="Skipped because partner circuit breaker is open.",
    )
    skipped_rate_limited: list[uuid.UUID] = Field(
        default_factory=list,
        description="Skipped because partner rate limit denied the request.",
    )
    skipped_invalid_status: list[uuid.UUID] = Field(
        default_factory=list,
        description="Skipped because delivery status is not failed.",
    )
    not_found: list[uuid.UUID] = Field(
        default_factory=list,
        description="Delivery public IDs that do not exist.",
    )


DEAD_LETTER_PURGE_EXAMPLES = {
    "false_positive": {
        "summary": "Tombstone after a contract fix",
        "value": {"reason": "False positive after contract fix."},
    },
}


class DeadLetterPurgeRequest(BaseModel):
    """Body for DELETE /admin/v1/dead-letters/{id}."""

    reason: str = Field(
        ...,
        description="Required admin reason for purge audit trail (non-empty).",
        examples=["False positive after contract fix."],
    )

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "reason must not be empty"
            raise ValueError(msg)
        return stripped


class DeadLetterAckResponse(BaseModel):
    """DLQ acknowledge response."""

    id: uuid.UUID = Field(..., description="Dead-letter public UUIDv7 identifier.")
    acknowledged_at: datetime = Field(..., description="Acknowledgement timestamp (UTC).")
    acknowledged_by: str = Field(..., description="Operator or admin principal id.")


class DeadLetterPurgeResponse(BaseModel):
    """DLQ purge (tombstone) response."""

    id: uuid.UUID = Field(..., description="Dead-letter public UUIDv7 identifier.")
    reason: str = Field(..., description="Terminal reason after purge (manual_purge).")

"""Partner admin request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import PartnerStatus
from app.schemas.common import PaginatedResponse

PARTNER_CREATE_EXAMPLES = {
    "acme": {
        "summary": "Provision a new partner",
        "description": "Creates a partner and returns the signing secret once.",
        "value": {
            "slug": "acme-erp",
            "name": "ACME ERP",
            "sla_seconds": 60,
        },
    },
}


class PartnerCreate(BaseModel):
    """Body for POST /admin/v1/partners."""

    slug: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
        description="URL-safe partner identifier (unique, lowercase).",
        examples=["acme-erp"],
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable partner display name.",
        examples=["ACME ERP"],
    )
    sla_seconds: int = Field(
        ...,
        ge=1,
        description="Target SLA in seconds for first successful delivery.",
        examples=[60],
    )
    rate_limit_rps: int | None = Field(
        default=None,
        ge=1,
        description="Optional inbound/outbound rate limit override (requests per second).",
    )


PARTNER_UPDATE_EXAMPLES = {
    "activate": {
        "summary": "Mark partner active",
        "value": {"status": "active"},
    },
}


class PartnerUpdate(BaseModel):
    """Body for PATCH /admin/v1/partners/{id}."""

    status: PartnerStatus | None = Field(
        default=None,
        description="Partner lifecycle status.",
    )
    sla_seconds: int | None = Field(
        default=None,
        ge=1,
        description="Updated SLA target in seconds.",
    )
    rate_limit_rps: int | None = Field(
        default=None,
        ge=1,
        description="Updated rate limit in requests per second.",
    )
    auto_replay_enabled: bool | None = Field(
        default=None,
        description="Whether scheduled auto-replay is enabled for this partner.",
    )
    circuit_breaker_config: dict[str, object] | None = Field(
        default=None,
        description="Circuit breaker configuration JSON object.",
    )


class PartnerResponse(BaseModel):
    """Partner details returned by admin GET endpoints (no secrets)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(
        ...,
        description="Partner public identifier (UUIDv7). Never the internal BIGINT key.",
    )
    slug: str = Field(..., description="URL-safe partner slug.")
    name: str = Field(..., description="Display name.")
    status: PartnerStatus = Field(..., description="Partner lifecycle status.")
    sla_seconds: int = Field(..., description="SLA target in seconds.")
    rate_limit_rps: int = Field(..., description="Rate limit in requests per second.")
    auto_replay_enabled: bool = Field(..., description="Scheduled auto-replay flag.")
    circuit_breaker_config: dict[str, object] = Field(
        ...,
        description="Circuit breaker configuration.",
    )
    created_at: datetime = Field(..., description="Creation timestamp (UTC).")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC).")


class PartnerCreatedResponse(PartnerResponse):
    """Partner creation response — includes signing secret once."""

    signing_secret: str = Field(
        ...,
        description="Plaintext HMAC signing secret. Shown only on create; store out-of-band.",
    )


class RotateSecretResponse(PartnerResponse):
    """Signing secret rotation response — plaintext shown once."""

    signing_secret: str = Field(
        ...,
        description="New plaintext HMAC signing secret. Shown only on rotate; store out-of-band.",
        examples=["whsec_R0tAt3dS3cr3tPlaintextOnce"],
    )


class ApiKeyCreate(BaseModel):
    """Body for POST /admin/v1/partners/{id}/api-keys."""

    scopes: list[str] = Field(
        default_factory=lambda: ["inbound:write"],
        description="OAuth-style scopes granted to the API key.",
        examples=[["inbound:write"]],
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Optional expiration timestamp (UTC).",
    )


API_KEY_CREATE_EXAMPLES = {
    "inbound_write": {
        "summary": "Inbound write key",
        "value": {"scopes": ["inbound:write"]},
    },
}


class ApiKeyCreatedResponse(BaseModel):
    """API key creation response — plaintext key shown once."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="API key public identifier (UUIDv7).")
    key: str = Field(
        ...,
        description="Full API key plaintext. Shown only once; store out-of-band.",
    )
    key_prefix: str = Field(
        ...,
        description="Key prefix for lookup and display (first 16 characters).",
    )
    scopes: list[str] = Field(..., description="Granted scopes.")
    expires_at: datetime | None = Field(
        default=None,
        description="Expiration timestamp if set.",
    )
    created_at: datetime = Field(..., description="Creation timestamp (UTC).")


class PaginatedPartnersResponse(PaginatedResponse[PartnerResponse]):
    """Paginated partner list."""

    items: list[PartnerResponse] = Field(..., description="Partners on this page.")

"""Analytics admin response schemas (spec §7.1.4)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.domain.services.circuit_breaker import AnalyticsCircuitState


class PartnerAnalyticsSummary(BaseModel):
    """24-hour partner delivery compliance summary."""

    id: uuid.UUID = Field(
        ...,
        description="Partner public UUIDv7 identifier.",
        examples=["0194a2b3-c4d5-7890-abcd-ef1234567890"],
    )
    slug: str = Field(
        ...,
        description="Partner slug (URL-safe identifier).",
        examples=["acme-erp"],
    )
    window_hours: int = Field(
        ...,
        description="Rolling analytics window length in hours.",
        examples=[24],
    )
    success_rate: float | None = Field(
        ...,
        description=(
            "Delivered divided by terminal deliveries (delivered + failed) in the window; "
            "null when there are no terminal rows."
        ),
        examples=[0.92],
    )
    p95_latency_ms: int | None = Field(
        ...,
        description=(
            "95th percentile of delivery attempt durations in the window (nearest-rank); "
            "null when no timed attempts exist."
        ),
        examples=[245],
    )
    sla_compliance_pct: float | None = Field(
        ...,
        description=(
            "Percentage of terminal deliveries without SLA breach in the window; "
            "null when there are no terminal rows."
        ),
        examples=[98.5],
    )
    sla_breaches: int = Field(
        ...,
        ge=0,
        description="Count of terminal deliveries with sla_breached=true in the window.",
        examples=[2],
    )
    circuit_state: AnalyticsCircuitState = Field(
        ...,
        description="Redis circuit breaker state; unknown when Redis is unavailable.",
        examples=["closed"],
    )
    dlq_age_seconds: int = Field(
        ...,
        ge=0,
        description=(
            "Age in seconds of the oldest unacknowledged dead-letter for this partner; "
            "0 when none."
        ),
        examples=[3600],
    )


class TopFailingPartner(BaseModel):
    """Partner ranked by low success rate in the analytics window."""

    id: uuid.UUID = Field(
        ...,
        description="Partner public UUIDv7 identifier.",
        examples=["0194a2b3-c4d5-7890-abcd-ef1234567890"],
    )
    slug: str = Field(
        ...,
        description="Partner slug.",
        examples=["acme-erp"],
    )
    success_rate: float = Field(
        ...,
        description="Delivered / terminal deliveries in the window.",
        examples=[0.45],
    )
    sla_breaches: int = Field(
        ...,
        ge=0,
        description="Terminal deliveries with SLA breach in the window.",
        examples=[5],
    )


class ComplianceExportRow(BaseModel):
    """Per-partner SLA compliance metrics for a reporting window."""

    partner_slug: str = Field(
        ...,
        description="Partner slug (primary identifier in CSV exports).",
        examples=["acme-erp"],
    )
    success_rate: float | None = Field(
        ...,
        description=(
            "Delivered divided by terminal deliveries in the window; "
            "null when the partner has no terminal rows."
        ),
        examples=[0.92],
    )
    sla_compliance_pct: float | None = Field(
        ...,
        description=(
            "Percentage of terminal deliveries without SLA breach in the window; "
            "null when the partner has no terminal rows."
        ),
        examples=[98.5],
    )
    sla_breaches: int = Field(
        ...,
        ge=0,
        description="Count of terminal deliveries with sla_breached=true in the window.",
        examples=[2],
    )
    dlq_count: int = Field(
        ...,
        ge=0,
        description=(
            "Dead-letter entries created for the partner in the window "
            "(excludes manual_purge tombstones)."
        ),
        examples=[3],
    )


class ComplianceExportResponse(BaseModel):
    """JSON compliance export payload."""

    rows: list[ComplianceExportRow] = Field(
        ...,
        description="One row per partner with activity in the requested window.",
    )


class AnalyticsOverview(BaseModel):
    """Fleet-wide analytics snapshot."""

    window_hours: int = Field(
        ...,
        description="Rolling analytics window length in hours.",
        examples=[24],
    )
    dlq_count: int = Field(
        ...,
        ge=0,
        description="Current dead-letter backlog (excludes manual_purge tombstones).",
        examples=[12],
    )
    avg_sla_compliance_pct: float | None = Field(
        ...,
        description=(
            "Mean per-partner SLA compliance among partners with terminal deliveries "
            "in the window; null when none."
        ),
        examples=[96.2],
    )
    top_failing_partners: list[TopFailingPartner] = Field(
        ...,
        description=("Up to five partners with lowest success_rate, then highest sla_breaches."),
    )

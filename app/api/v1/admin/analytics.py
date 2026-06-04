"""Admin analytics routes (spec §7.1.4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from fastapi.responses import PlainTextResponse, Response

from app.api.auth import AdminPrincipal, RequireViewer
from app.api.deps import DbSession, NowTimestamp, RedisClient
from app.api.v1.admin.partners import get_partner_by_public_id
from app.config import Settings, get_settings
from app.domain.services.analytics_service import (
    build_analytics_overview,
    build_compliance_export,
    build_partner_summary,
    render_compliance_csv,
)
from app.schemas.analytics import (
    AnalyticsOverview,
    ComplianceExportResponse,
    ComplianceExportRow,
    PartnerAnalyticsSummary,
    TopFailingPartner,
)
from app.schemas.common import AdminErrorResponse

router = APIRouter(prefix="/admin/v1/analytics", tags=["admin"])

_ADMIN_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": AdminErrorResponse, "description": "Missing or invalid admin credentials."},
    403: {"model": AdminErrorResponse, "description": "Insufficient role for this operation."},
    404: {"model": AdminErrorResponse, "description": "Partner not found."},
    422: {"model": AdminErrorResponse, "description": "Validation error."},
}


@router.get(
    "/partners/{id}/summary",
    response_model=PartnerAnalyticsSummary,
    responses=_ADMIN_ERRORS,
    summary="Partner analytics summary",
    description=(
        "Rolling 24-hour delivery compliance metrics from PostgreSQL "
        "(success rate, p95 latency, SLA compliance, circuit state, DLQ age)."
    ),
)
async def get_partner_analytics_summary(
    id: Annotated[uuid.UUID, Path(description="Partner public UUIDv7 identifier.")],
    session: DbSession,
    redis: RedisClient,
    settings: Annotated[Settings, Depends(get_settings)],
    now_ts: NowTimestamp,
    _principal: Annotated[AdminPrincipal, Depends(RequireViewer)],
) -> PartnerAnalyticsSummary:
    partner = await get_partner_by_public_id(session, id)
    now = datetime.fromtimestamp(now_ts, tz=UTC)
    summary = await build_partner_summary(
        session,
        partner=partner,
        redis=redis,
        settings=settings,
        now=now,
    )
    return PartnerAnalyticsSummary(
        id=summary.id,
        slug=summary.slug,
        window_hours=summary.window_hours,
        success_rate=summary.success_rate,
        p95_latency_ms=summary.p95_latency_ms,
        sla_compliance_pct=summary.sla_compliance_pct,
        sla_breaches=summary.sla_breaches,
        circuit_state=summary.circuit_state,
        dlq_age_seconds=summary.dlq_age_seconds,
    )


@router.get(
    "/overview",
    response_model=AnalyticsOverview,
    responses=_ADMIN_ERRORS,
    summary="Fleet analytics overview",
    description=(
        "Rolling 24-hour fleet snapshot: DLQ backlog, average SLA compliance, "
        "and top failing partners by success rate."
    ),
)
async def get_analytics_overview(
    session: DbSession,
    now_ts: NowTimestamp,
    _principal: Annotated[AdminPrincipal, Depends(RequireViewer)],
) -> AnalyticsOverview:
    now = datetime.fromtimestamp(now_ts, tz=UTC)
    overview = await build_analytics_overview(session, now=now)
    return AnalyticsOverview(
        window_hours=overview.window_hours,
        dlq_count=overview.dlq_count,
        avg_sla_compliance_pct=overview.avg_sla_compliance_pct,
        top_failing_partners=[
            TopFailingPartner(
                id=item.id,
                slug=item.slug,
                success_rate=item.success_rate,
                sla_breaches=item.sla_breaches,
            )
            for item in overview.top_failing_partners
        ],
    )


@router.get(
    "/compliance-export",
    response_model=ComplianceExportResponse,
    responses={
        **_ADMIN_ERRORS,
        200: {
            "description": "Compliance export for the requested window.",
            "content": {
                "text/csv": {"schema": {"type": "string", "format": "binary"}},
            },
        },
    },
    summary="Weekly SLA compliance export",
    description=(
        "Export per-partner success rate, SLA compliance, breaches, and DLQ counts "
        "for a custom ISO-8601 time window. Default response is CSV; send "
        "Accept: application/json for a structured payload."
    ),
)
async def get_compliance_export(
    session: DbSession,
    from_dt: Annotated[
        datetime,
        Query(alias="from", description="Inclusive window start (ISO 8601)."),
    ],
    to_dt: Annotated[
        datetime,
        Query(alias="to", description="Inclusive window end (ISO 8601)."),
    ],
    _principal: Annotated[AdminPrincipal, Depends(RequireViewer)],
    accept: Annotated[
        str | None,
        Header(description="text/csv (default) or application/json."),
    ] = None,
) -> ComplianceExportResponse | Response:
    if to_dt <= from_dt:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'to' must be after 'from'.",
        )
    rows = await build_compliance_export(
        session,
        window_start=from_dt,
        window_end=to_dt,
    )
    if accept and "application/json" in accept:
        return ComplianceExportResponse(
            rows=[
                ComplianceExportRow(
                    partner_slug=row.partner_slug,
                    success_rate=row.success_rate,
                    sla_compliance_pct=row.sla_compliance_pct,
                    sla_breaches=row.sla_breaches,
                    dlq_count=row.dlq_count,
                )
                for row in rows
            ]
        )
    return PlainTextResponse(
        content=render_compliance_csv(rows),
        media_type="text/csv",
    )

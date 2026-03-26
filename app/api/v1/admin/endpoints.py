"""Admin partner endpoint routes (spec §7.1.2)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import AdminPrincipal, RequireAdmin, RequireViewer
from app.api.deps import DbSession
from app.api.v1.admin.mappers import endpoint_to_response
from app.api.v1.admin.partners import get_partner_by_public_id
from app.config import Settings, get_settings
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.partner import Partner
from app.domain.services.accept_path_cache import invalidate_partner_endpoints
from app.schemas.common import AdminErrorResponse
from app.schemas.endpoint import (
    ENDPOINT_CREATE_EXAMPLES,
    ENDPOINT_UPDATE_EXAMPLES,
    EndpointCreate,
    EndpointResponse,
    EndpointUpdate,
)

router = APIRouter(prefix="/admin/v1", tags=["admin"])

_ADMIN_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": AdminErrorResponse, "description": "Missing or invalid admin credentials."},
    403: {"model": AdminErrorResponse, "description": "Insufficient role for this operation."},
    404: {"model": AdminErrorResponse, "description": "Endpoint not found."},
    422: {"model": AdminErrorResponse, "description": "Validation error."},
}


async def _get_endpoint(session: AsyncSession, endpoint_id: uuid.UUID) -> PartnerEndpoint:
    result = await session.execute(
        select(PartnerEndpoint).where(PartnerEndpoint.id == endpoint_id)
    )
    endpoint = result.scalar_one_or_none()
    if endpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not found.")
    return endpoint


async def _partner_public_id(session: AsyncSession, endpoint: PartnerEndpoint) -> uuid.UUID:
    result = await session.execute(
        select(Partner.public_id).where(Partner.id == endpoint.partner_id)
    )
    public_id = result.scalar_one_or_none()
    if public_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Partner not found.")
    return public_id


@router.post(
    "/partners/{id}/endpoints",
    status_code=status.HTTP_201_CREATED,
    response_model=EndpointResponse,
    responses=_ADMIN_ERRORS,
    summary="Create partner endpoint",
    description=(
        "Registers an inbound or outbound HTTP endpoint for the partner. "
        "Outbound URLs receive signed webhooks; `event_types` selects fan-out."
    ),
)
async def create_endpoint(
    id: Annotated[uuid.UUID, Path(description="Partner public UUIDv7 identifier.")],
    body: Annotated[EndpointCreate, Body(openapi_examples=ENDPOINT_CREATE_EXAMPLES)],
    session: DbSession,
    _principal: Annotated[AdminPrincipal, Depends(RequireAdmin)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EndpointResponse:
    partner = await get_partner_by_public_id(session, id)
    endpoint = PartnerEndpoint(
        partner_id=partner.id,
        direction=body.direction.value,
        url=body.url,
        event_types=body.event_types,
        sla_seconds=body.sla_seconds,
        max_attempts=body.max_attempts or settings.hub_max_attempts_default,
        timeout_connect_ms=body.timeout_connect_ms or settings.hub_http_connect_timeout_ms,
        timeout_read_ms=body.timeout_read_ms or settings.hub_http_read_timeout_ms,
    )
    session.add(endpoint)
    await session.commit()
    invalidate_partner_endpoints(partner.id)
    await session.refresh(endpoint)
    return endpoint_to_response(endpoint, partner.public_id)


@router.get(
    "/endpoints/{id}",
    response_model=EndpointResponse,
    responses=_ADMIN_ERRORS,
    summary="Get endpoint",
    description="Returns endpoint configuration. `id` is the endpoint UUIDv7.",
)
async def get_endpoint(
    id: Annotated[uuid.UUID, Path(description="Endpoint UUIDv7 identifier.")],
    session: DbSession,
    _principal: Annotated[AdminPrincipal, Depends(RequireViewer)],
) -> EndpointResponse:
    endpoint = await _get_endpoint(session, id)
    public_id = await _partner_public_id(session, endpoint)
    return endpoint_to_response(endpoint, public_id)


@router.patch(
    "/endpoints/{id}",
    response_model=EndpointResponse,
    responses=_ADMIN_ERRORS,
    summary="Update endpoint",
    description=(
        "Partial update of URL, event types, SLA, or status (active / paused / disabled)."
    ),
)
async def update_endpoint(
    id: Annotated[uuid.UUID, Path(description="Endpoint UUIDv7 identifier.")],
    body: Annotated[EndpointUpdate, Body(openapi_examples=ENDPOINT_UPDATE_EXAMPLES)],
    session: DbSession,
    _principal: Annotated[AdminPrincipal, Depends(RequireAdmin)],
) -> EndpointResponse:
    endpoint = await _get_endpoint(session, id)
    if body.status is not None:
        endpoint.status = body.status.value
    if body.url is not None:
        endpoint.url = body.url
    if body.event_types is not None:
        endpoint.event_types = body.event_types
    if body.sla_seconds is not None:
        endpoint.sla_seconds = body.sla_seconds
    await session.commit()
    invalidate_partner_endpoints(endpoint.partner_id)
    await session.refresh(endpoint)
    public_id = await _partner_public_id(session, endpoint)
    return endpoint_to_response(endpoint, public_id)

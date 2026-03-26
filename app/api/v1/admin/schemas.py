"""Admin payload schema registry routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy import func, select

from app.api.auth import AdminPrincipal, RequireAdmin, RequireViewer
from app.api.deps import DbSession
from app.domain.enums import PayloadSchemaStatus
from app.domain.models.payload_schema import PayloadSchema
from app.domain.services.accept_path_cache import invalidate_schema
from app.schemas.common import AdminErrorResponse
from app.schemas.payload_schema import (
    SCHEMA_CREATE_EXAMPLES,
    PayloadSchemaCreate,
    PayloadSchemaListResponse,
    PayloadSchemaResponse,
)

router = APIRouter(prefix="/admin/v1", tags=["admin"])

_ADMIN_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": AdminErrorResponse, "description": "Missing or invalid admin credentials."},
    403: {"model": AdminErrorResponse, "description": "Insufficient role for this operation."},
    422: {"model": AdminErrorResponse, "description": "Validation error."},
}


@router.post(
    "/schemas",
    status_code=status.HTTP_201_CREATED,
    response_model=PayloadSchemaResponse,
    responses=_ADMIN_ERRORS,
    summary="Register payload JSON Schema",
    description=(
        "Registers a JSON Schema (Draft 2020-12) for an event type. "
        "Inbound and outbound payloads are validated against the latest active version."
    ),
)
async def create_payload_schema(
    body: Annotated[PayloadSchemaCreate, Body(openapi_examples=SCHEMA_CREATE_EXAMPLES)],
    session: DbSession,
    _principal: Annotated[AdminPrincipal, Depends(RequireAdmin)],
) -> PayloadSchemaResponse:
    row = PayloadSchema(
        event_type=body.event_type,
        version=body.version,
        json_schema=body.json_schema,
        status=PayloadSchemaStatus.ACTIVE,
    )
    session.add(row)
    await session.commit()
    invalidate_schema(body.event_type)
    await session.refresh(row)
    return PayloadSchemaResponse.model_validate(row)


@router.get(
    "/schemas",
    response_model=PayloadSchemaListResponse,
    responses=_ADMIN_ERRORS,
    summary="List payload schemas for an event type",
    description="Lists schema versions for `event_type`, newest first.",
)
async def list_payload_schemas(
    session: DbSession,
    event_type: Annotated[str, Query(description="Filter by event type name.")],
    _principal: Annotated[AdminPrincipal, Depends(RequireViewer)],
) -> PayloadSchemaListResponse:
    count_result = await session.execute(
        select(func.count())
        .select_from(PayloadSchema)
        .where(PayloadSchema.event_type == event_type)
    )
    total = count_result.scalar_one()
    result = await session.execute(
        select(PayloadSchema)
        .where(PayloadSchema.event_type == event_type)
        .order_by(PayloadSchema.version.desc())
    )
    rows = result.scalars().all()
    items = [PayloadSchemaResponse.model_validate(row) for row in rows]
    return PayloadSchemaListResponse(items=items, total=total)

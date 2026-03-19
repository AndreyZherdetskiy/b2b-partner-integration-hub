"""Admin partner and API key routes (spec §7.1.2)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import AdminPrincipal, RequireAdmin, RequireViewer
from app.api.deps import DbSession, Pagination
from app.api.v1.admin.mappers import partner_to_response
from app.config import Settings, get_settings
from app.domain.enums import PartnerStatus
from app.domain.models.api_key import PartnerApiKey
from app.domain.models.partner import Partner
from app.domain.services.accept_path_cache import invalidate_partner
from app.domain.services.api_keys import generate_api_key, generate_signing_secret
from app.domain.services.secrets import encrypt_signing_secret
from app.domain.services.signing_secrets import (
    insert_primary_signing_secret,
    rotate_partner_signing_secret,
)
from app.schemas.common import AdminErrorResponse
from app.schemas.partner import (
    API_KEY_CREATE_EXAMPLES,
    PARTNER_CREATE_EXAMPLES,
    PARTNER_UPDATE_EXAMPLES,
    ApiKeyCreate,
    ApiKeyCreatedResponse,
    PaginatedPartnersResponse,
    PartnerCreate,
    PartnerCreatedResponse,
    PartnerResponse,
    PartnerUpdate,
    RotateSecretResponse,
)

router = APIRouter(prefix="/admin/v1/partners", tags=["admin"])

_ADMIN_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": AdminErrorResponse, "description": "Missing or invalid admin credentials."},
    403: {"model": AdminErrorResponse, "description": "Insufficient role for this operation."},
    404: {"model": AdminErrorResponse, "description": "Partner not found."},
    422: {"model": AdminErrorResponse, "description": "Validation error."},
}


async def get_partner_by_public_id(session: AsyncSession, partner_id: uuid.UUID) -> Partner:
    result = await session.execute(select(Partner).where(Partner.public_id == partner_id))
    partner = result.scalar_one_or_none()
    if partner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Partner not found.")
    return partner


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=PartnerCreatedResponse,
    responses=_ADMIN_ERRORS,
    summary="Create partner",
    description=(
        "Provisions a partner and returns the HMAC signing secret once. "
        "`id` in the response is the public UUIDv7, never the sequential database key."
    ),
)
async def create_partner(
    body: Annotated[PartnerCreate, Body(openapi_examples=PARTNER_CREATE_EXAMPLES)],
    session: DbSession,
    _principal: Annotated[AdminPrincipal, Depends(RequireAdmin)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PartnerCreatedResponse:
    if not settings.fernet_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="FERNET_KEY is not configured.",
        )
    signing_secret = generate_signing_secret()
    encrypted = encrypt_signing_secret(signing_secret.encode("utf-8"), settings.fernet_key)
    partner = Partner(
        slug=body.slug,
        name=body.name,
        status=PartnerStatus.PROVISIONING,
        sla_seconds=body.sla_seconds,
        rate_limit_rps=body.rate_limit_rps or settings.hub_rate_limit_rps_default,
        signing_secret_encrypted=encrypted,
    )
    session.add(partner)
    try:
        await session.flush()
        insert_primary_signing_secret(
            session,
            partner_id=partner.id,
            secret_encrypted=encrypted,
            version=1,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Partner slug already exists.",
        ) from exc
    invalidate_partner(partner.public_id)
    await session.refresh(partner)
    base = partner_to_response(partner)
    return PartnerCreatedResponse(**base.model_dump(), signing_secret=signing_secret)


@router.get(
    "",
    response_model=PaginatedPartnersResponse,
    responses=_ADMIN_ERRORS,
    summary="List partners",
    description="Paginated partner directory. Identifiers are public UUIDv7 values.",
)
async def list_partners(
    session: DbSession,
    pagination: Pagination,
    _principal: Annotated[AdminPrincipal, Depends(RequireViewer)],
) -> PaginatedPartnersResponse:
    count_stmt = select(func.count()).select_from(Partner)
    total = int((await session.execute(count_stmt)).scalar_one())
    stmt = (
        select(Partner)
        .order_by(Partner.created_at.desc())
        .limit(pagination.limit)
        .offset(pagination.offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return PaginatedPartnersResponse(
        items=[partner_to_response(p) for p in rows],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get(
    "/{id}",
    response_model=PartnerResponse,
    responses=_ADMIN_ERRORS,
    summary="Get partner",
    description=(
        "Returns partner configuration by public UUIDv7. Signing secrets are never included."
    ),
)
async def get_partner(
    id: Annotated[uuid.UUID, Path(description="Partner public UUIDv7 identifier.")],
    session: DbSession,
    _principal: Annotated[AdminPrincipal, Depends(RequireViewer)],
) -> PartnerResponse:
    partner = await get_partner_by_public_id(session, id)
    return partner_to_response(partner)


@router.patch(
    "/{id}",
    response_model=PartnerResponse,
    responses=_ADMIN_ERRORS,
    summary="Update partner",
    description=(
        "Partial update of status, SLA, rate limit, auto-replay, or circuit-breaker config."
    ),
)
async def update_partner(
    id: Annotated[uuid.UUID, Path(description="Partner public UUIDv7 identifier.")],
    body: Annotated[PartnerUpdate, Body(openapi_examples=PARTNER_UPDATE_EXAMPLES)],
    session: DbSession,
    _principal: Annotated[AdminPrincipal, Depends(RequireAdmin)],
) -> PartnerResponse:
    partner = await get_partner_by_public_id(session, id)
    if body.status is not None:
        partner.status = body.status.value
    if body.sla_seconds is not None:
        partner.sla_seconds = body.sla_seconds
    if body.rate_limit_rps is not None:
        partner.rate_limit_rps = body.rate_limit_rps
    if body.auto_replay_enabled is not None:
        partner.auto_replay_enabled = body.auto_replay_enabled
    if body.circuit_breaker_config is not None:
        partner.circuit_breaker_config = body.circuit_breaker_config
    await session.commit()
    invalidate_partner(partner.public_id)
    await session.refresh(partner)
    return partner_to_response(partner)


@router.post(
    "/{id}/api-keys",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiKeyCreatedResponse,
    responses=_ADMIN_ERRORS,
    summary="Create API key",
    description=(
        "Issues a partner API key. The full key is returned once; store it out-of-band. "
        "Typical scopes: `inbound:write`, `status:read`."
    ),
)
async def create_api_key(
    id: Annotated[uuid.UUID, Path(description="Partner public UUIDv7 identifier.")],
    body: Annotated[ApiKeyCreate, Body(openapi_examples=API_KEY_CREATE_EXAMPLES)],
    session: DbSession,
    _principal: Annotated[AdminPrincipal, Depends(RequireAdmin)],
) -> ApiKeyCreatedResponse:
    partner = await get_partner_by_public_id(session, id)
    full_key, prefix, key_hash = generate_api_key()
    api_key = PartnerApiKey(
        partner_id=partner.id,
        key_prefix=prefix,
        key_hash=key_hash,
        scopes=body.scopes,
        expires_at=body.expires_at,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    return ApiKeyCreatedResponse(
        id=api_key.id,
        key=full_key,
        key_prefix=prefix,
        scopes=list(api_key.scopes),
        expires_at=api_key.expires_at,
        created_at=api_key.created_at,
    )


@router.post(
    "/{id}/rotate-secret",
    response_model=RotateSecretResponse,
    responses=_ADMIN_ERRORS,
    summary="Rotate partner signing secret",
    description=(
        "Rotates the partner HMAC signing secret with an overlap window "
        "(`hub_secret_rotation_overlap_hours`, default 24h). "
        "The previous secret remains valid for inbound verification until "
        "`valid_until`. Returns the new plaintext secret once."
    ),
)
async def rotate_signing_secret(
    id: Annotated[uuid.UUID, Path(description="Partner public UUIDv7 identifier.")],
    session: DbSession,
    principal: Annotated[AdminPrincipal, Depends(RequireAdmin)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RotateSecretResponse:
    if not settings.fernet_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="FERNET_KEY is not configured.",
        )
    partner = await get_partner_by_public_id(session, id)
    plaintext = await rotate_partner_signing_secret(
        session,
        partner,
        settings,
        actor_id=principal.sub,
    )
    await session.refresh(partner)
    base = partner_to_response(partner)
    return RotateSecretResponse(**base.model_dump(), signing_secret=plaintext)

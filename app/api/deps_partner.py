"""Partner API key authentication (Bearer, prefix + hash lookup)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select

from app.api.deps import DbSession
from app.domain.models.api_key import PartnerApiKey
from app.domain.services.api_keys import extract_prefix, verify_api_key

STATUS_READ_SCOPE = "status:read"


@dataclass(frozen=True)
class AuthenticatedPartner:
    """Resolved partner from a valid status:read API key."""

    partner_id: int


def parse_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


async def authenticate_partner_api_key(
    session: DbSession,
    authorization: Annotated[
        str | None,
        Header(
            description=(
                "Bearer partner API key with `status:read`. Missing or invalid returns 401; "
                "wrong scope returns 403."
            ),
        ),
    ] = None,
) -> AuthenticatedPartner:
    """Validate Bearer API key with status:read scope."""
    api_key = parse_bearer_token(authorization)
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
        )
    prefix = extract_prefix(api_key)
    result = await session.execute(select(PartnerApiKey).where(PartnerApiKey.key_prefix == prefix))
    rows = result.scalars().all()
    now_dt = datetime.now(UTC)
    matched_wrong_scope = False
    for row in rows:
        if row.revoked_at is not None:
            continue
        if row.expires_at is not None and row.expires_at <= now_dt:
            continue
        if not verify_api_key(api_key, row.key_hash):
            continue
        if STATUS_READ_SCOPE not in row.scopes:
            matched_wrong_scope = True
            continue
        return AuthenticatedPartner(partner_id=row.partner_id)
    if matched_wrong_scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key lacks status:read scope.",
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid API key.",
    )


PartnerAuth = Annotated[AuthenticatedPartner, Depends(authenticate_partner_api_key)]

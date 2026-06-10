"""Admin API authentication (bootstrap token + HS256 JWT stub)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError

from app.config import Settings, get_settings
from app.domain.enums import HubRole

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    """Authenticated admin caller."""

    sub: str
    role: HubRole


def _authenticate_token(token: str, settings: Settings) -> AdminPrincipal:
    if not settings.admin_bootstrap_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication is not configured.",
        )
    if token == settings.admin_bootstrap_token:
        return AdminPrincipal(sub="bootstrap", role=HubRole.HUB_ADMIN)
    try:
        payload = jwt.decode(
            token,
            settings.admin_bootstrap_token,
            algorithms=["HS256"],
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired admin token.",
        ) from exc
    role_raw = payload.get("role")
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin token claims.",
        )
    if not isinstance(role_raw, str) or not role_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin token role.",
        )
    try:
        role = HubRole(role_raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin token role.",
        ) from exc
    return AdminPrincipal(sub=sub, role=role)


async def get_admin_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AdminPrincipal:
    """Resolve admin principal from Bearer bootstrap token or HS256 JWT."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
        )
    return _authenticate_token(credentials.credentials, settings)


def require_roles(*allowed: HubRole) -> Callable[..., Awaitable[AdminPrincipal]]:
    """FastAPI dependency factory enforcing RBAC role membership."""

    async def _dep(
        principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    ) -> AdminPrincipal:
        if principal.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role for this operation.",
            )
        return principal

    return _dep


RequireViewer = require_roles(HubRole.HUB_ADMIN, HubRole.HUB_OPERATOR, HubRole.HUB_VIEWER)
RequireOperator = require_roles(HubRole.HUB_ADMIN, HubRole.HUB_OPERATOR)
RequireAdmin = require_roles(HubRole.HUB_ADMIN)

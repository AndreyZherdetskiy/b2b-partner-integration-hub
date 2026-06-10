"""API exception handlers for domain errors."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.errors import HubError


async def hub_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, HubError):
        raise exc
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HubError, hub_error_handler)

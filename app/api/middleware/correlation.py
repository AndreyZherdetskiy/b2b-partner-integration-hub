"""UUIDv7 correlation ID middleware (spec §2.5)."""

from __future__ import annotations

import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.domain.ids import generate_uuidv7
from app.logging import bind_correlation_id, clear_correlation_id

CORRELATION_HEADER = "X-Correlation-Id"


def _correlation_validation_error(raw: str) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {
                    "type": "uuid_version",
                    "loc": ["header", CORRELATION_HEADER],
                    "msg": "X-Correlation-Id must be a UUID version 7",
                    "input": raw,
                }
            ]
        },
    )


def resolve_correlation_id(raw: str | None) -> str | JSONResponse:
    if raw is None or raw.strip() == "":
        return str(generate_uuidv7())
    try:
        parsed = uuid.UUID(raw)
    except ValueError:
        return _correlation_validation_error(raw)
    if parsed.version != 7:
        return _correlation_validation_error(raw)
    return str(parsed)


class CorrelationMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        raw = headers.get(CORRELATION_HEADER)
        resolved = resolve_correlation_id(raw)
        if isinstance(resolved, JSONResponse):
            await resolved(scope, receive, send)
            return

        token = bind_correlation_id(resolved)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(raw=message["headers"])
                response_headers.append(CORRELATION_HEADER, resolved)
                message = {**message, "headers": response_headers.raw}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            clear_correlation_id(token)

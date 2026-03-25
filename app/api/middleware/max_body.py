"""Request body size limit middleware (spec §8.1 — Stage 1 256 KB)."""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self._max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                await _payload_too_large_response()(scope, receive, send)
                return
            if declared > self._max_body_size:
                await _payload_too_large_response()(scope, receive, send)
                return

        await self.app(scope, receive, send)


def _payload_too_large_response() -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "detail": [
                {
                    "type": "payload_too_large",
                    "loc": ["body"],
                    "msg": "Request body exceeds the 256 KB payload limit",
                    "input": None,
                }
            ]
        },
    )

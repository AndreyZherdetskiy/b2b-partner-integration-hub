"""FastAPI partner webhook mock with chaos scenarios via X-Mock-Scenario."""

import asyncio
import os
from enum import StrEnum
from typing import assert_never

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

MOCK_TIMEOUT_SECONDS = float(os.getenv("MOCK_TIMEOUT_SECONDS", "30"))


class MockScenario(StrEnum):
    OK = "ok"
    FAIL_503 = "fail_503"
    FAIL_503_THEN_OK = "fail_503_then_ok"
    FAIL_400 = "fail_400"
    TIMEOUT = "timeout"
    FAIL_429 = "fail_429"


_FAIL_503_THEN_OK_LIMIT = 3
_fail_503_then_ok_counts: dict[str, int] = {}


def _parse_scenario(raw: str | None) -> MockScenario | None:
    if not raw:
        return None
    try:
        return MockScenario(raw.strip().lower())
    except ValueError:
        return None


def _fail_503_then_ok_response(request: Request) -> JSONResponse:
    idem = request.headers.get("Idempotency-Key", "")
    attempt = _fail_503_then_ok_counts.get(idem, 0) + 1
    _fail_503_then_ok_counts[idem] = attempt
    if attempt <= _FAIL_503_THEN_OK_LIMIT:
        return JSONResponse(
            {"status": "error", "scenario": MockScenario.FAIL_503_THEN_OK.value},
            status_code=503,
        )
    return JSONResponse(
        {"status": "ok", "scenario": MockScenario.FAIL_503_THEN_OK.value},
    )


async def _handle_webhook(request: Request, path_scenario: str | None = None) -> Response:
    scenario = _parse_scenario(request.headers.get("X-Mock-Scenario"))
    if scenario is None and path_scenario is not None:
        scenario = _parse_scenario(path_scenario)

    if scenario is None:
        return JSONResponse({"status": "ok", "scenario": "default"})

    match scenario:
        case MockScenario.OK:
            return JSONResponse({"status": "ok", "scenario": scenario.value})
        case MockScenario.FAIL_400:
            return JSONResponse(
                {"status": "error", "scenario": scenario.value},
                status_code=400,
            )
        case MockScenario.FAIL_503:
            return JSONResponse(
                {"status": "error", "scenario": scenario.value},
                status_code=503,
            )
        case MockScenario.FAIL_429:
            return JSONResponse(
                {"status": "error", "scenario": scenario.value},
                status_code=429,
            )
        case MockScenario.FAIL_503_THEN_OK:
            return _fail_503_then_ok_response(request)
        case MockScenario.TIMEOUT:
            await asyncio.sleep(MOCK_TIMEOUT_SECONDS)
            return JSONResponse({"status": "ok", "scenario": scenario.value})
        case _:
            assert_never(scenario)


def create_app() -> FastAPI:
    app = FastAPI(title="Partner Mock", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook")
    async def webhook(request: Request) -> Response:
        return await _handle_webhook(request)

    @app.post("/webhook/{scenario_name}")
    async def webhook_path(scenario_name: str, request: Request) -> Response:
        return await _handle_webhook(request, path_scenario=scenario_name)

    return app


app = create_app()

"""Correlation header and max body middleware (Task 7, spec §2.5 / §8.1)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from uuid6 import uuid7

from app.api.middleware.correlation import CorrelationMiddleware
from app.api.middleware.max_body import MaxBodySizeMiddleware
from app.config import get_settings
from app.main import create_app

CORRELATION_HEADER = "X-Correlation-Id"
MAX_BODY_BYTES = 256 * 1024


@pytest.fixture
def client() -> TestClient:
    app = create_app()

    @app.post("/test/body", tags=["internal"])
    def accept_body() -> dict[str, str]:
        return {"status": "ok"}

    return TestClient(app)


def test_missing_correlation_generates_uuidv7(client: TestClient) -> None:
    response = client.get("/inbound/v1/health")
    assert response.status_code == 200
    correlation_id = response.headers[CORRELATION_HEADER]
    parsed = uuid.UUID(correlation_id)
    assert parsed.version == 7


def test_valid_correlation_echoed(client: TestClient) -> None:
    correlation_id = str(uuid7())
    response = client.get(
        "/inbound/v1/health",
        headers={CORRELATION_HEADER: correlation_id},
    )
    assert response.status_code == 200
    assert response.headers[CORRELATION_HEADER] == correlation_id


def test_correlation_id_case_variant_accepted(client: TestClient) -> None:
    correlation_id = str(uuid7())
    response = client.get(
        "/inbound/v1/health",
        headers={"X-Correlation-ID": correlation_id},
    )
    assert response.status_code == 200
    assert response.headers[CORRELATION_HEADER] == correlation_id


def test_invalid_correlation_uuid_returns_422(client: TestClient) -> None:
    response = client.get(
        "/inbound/v1/health",
        headers={CORRELATION_HEADER: "not-a-uuid"},
    )
    assert response.status_code == 422
    assert (
        CORRELATION_HEADER not in response.headers
        or response.headers.get(CORRELATION_HEADER) != "not-a-uuid"
    )


def test_uuidv4_correlation_returns_422(client: TestClient) -> None:
    response = client.get(
        "/inbound/v1/health",
        headers={CORRELATION_HEADER: "550e8400-e29b-41d4-a716-446655440000"},
    )
    assert response.status_code == 422


def test_body_within_limit_accepted(client: TestClient) -> None:
    body = b"x" * 1024
    response = client.post("/test/body", content=body)
    assert response.status_code == 200
    assert response.headers[CORRELATION_HEADER]


def test_body_over_limit_returns_413(client: TestClient) -> None:
    body = b"x" * (MAX_BODY_BYTES + 1)
    response = client.post("/test/body", content=body)
    assert response.status_code == 413


def test_max_body_limit_from_settings() -> None:
    settings = get_settings()
    assert settings.hub_max_payload_bytes == MAX_BODY_BYTES


def test_correlation_middleware_is_pure_asgi() -> None:
    assert not issubclass(CorrelationMiddleware, BaseHTTPMiddleware)


def test_max_body_middleware_is_pure_asgi() -> None:
    assert not issubclass(MaxBodySizeMiddleware, BaseHTTPMiddleware)

"""Unit tests for load harness helpers (no Locust / Compose required)."""

from __future__ import annotations

import httpx
import pytest
from loadtests.config import admin_token, load_host, load_wait_bounds, partner_slug
from loadtests.preflight import (
    READY_PATH,
    PreflightError,
    assert_minimum_requests,
    preflight_api_ready,
    preflight_credentials,
    resolve_partner_public_id,
    run_smoke_preflight,
)

DEMO_TOKEN = "demo-admin-bootstrap-token-not-for-prod"
ACME_ID = "0194a2b3-c4d5-7890-abcd-ef1234567890"


def test_load_host_prefers_load_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOAD_HOST", "http://127.0.0.1:9999/")
    monkeypatch.setenv("BASE_URL", "http://example:8000")
    assert load_host() == "http://127.0.0.1:9999"


def test_load_host_falls_back_to_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_HOST", raising=False)
    monkeypatch.setenv("BASE_URL", "http://api.example:8000/")
    assert load_host() == "http://api.example:8000"


def test_load_host_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_HOST", raising=False)
    monkeypatch.delenv("BASE_URL", raising=False)
    assert load_host() == "http://127.0.0.1:8000"


def test_admin_token_prefers_load_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOAD_ADMIN_TOKEN", "from-load")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN", "from-bootstrap")
    assert admin_token() == "from-load"


def test_admin_token_uses_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN", "from-bootstrap")
    assert admin_token() == "from-bootstrap"


def test_preflight_credentials_rejects_missing_process_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Must fail even when repo `.env` has ADMIN_BOOTSTRAP_TOKEN (no pydantic env_file leak)."""
    monkeypatch.delenv("ADMIN_BOOTSTRAP_TOKEN", raising=False)
    monkeypatch.delenv("LOAD_ADMIN_TOKEN", raising=False)
    with pytest.raises(PreflightError, match="ADMIN_BOOTSTRAP_TOKEN"):
        preflight_credentials()


def test_preflight_credentials_accepts_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN", DEMO_TOKEN)
    assert preflight_credentials() == DEMO_TOKEN


def test_ready_path_is_inbound_health() -> None:
    assert READY_PATH == "/inbound/v1/health"


def test_preflight_api_ready_accepts_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/inbound/v1/health"
        return httpx.Response(200, json={"status": "ok"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    preflight_api_ready(host="http://test", client=client)


def test_preflight_api_ready_rejects_503() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"status": "not_ready"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    with pytest.raises(PreflightError, match="health"):
        preflight_api_ready(host="http://test", client=client)


def test_preflight_api_ready_rejects_transport_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    with pytest.raises(PreflightError, match="health"):
        preflight_api_ready(host="http://test", client=client)


def test_resolve_partner_uses_env_without_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOAD_PARTNER_PUBLIC_ID", ACME_ID)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call partners list when env id is set")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    resolved = resolve_partner_public_id(host="http://test", token=DEMO_TOKEN, client=client)
    assert resolved == ACME_ID


def test_resolve_partner_finds_slug_in_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_PARTNER_PUBLIC_ID", raising=False)
    monkeypatch.setenv("LOAD_PARTNER_SLUG", "acme-erp")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/admin/v1/partners"
        assert request.headers["Authorization"] == f"Bearer {DEMO_TOKEN}"
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": ACME_ID, "slug": "acme-erp", "name": "Acme ERP", "status": "active"},
                    {
                        "id": "0194ffff-ffff-7fff-8000-000000000001",
                        "slug": "flaky-logistics",
                        "name": "Flaky",
                        "status": "active",
                    },
                ],
                "total": 2,
                "limit": 50,
                "offset": 0,
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    resolved = resolve_partner_public_id(host="http://test", token=DEMO_TOKEN, client=client)
    assert resolved == ACME_ID


def test_resolve_partner_errors_when_slug_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_PARTNER_PUBLIC_ID", raising=False)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [], "total": 0, "limit": 50, "offset": 0},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    with pytest.raises(PreflightError, match="acme-erp"):
        resolve_partner_public_id(host="http://test", token=DEMO_TOKEN, client=client)


def test_run_smoke_preflight_requires_credentials_before_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADMIN_BOOTSTRAP_TOKEN", raising=False)
    monkeypatch.delenv("LOAD_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("LOAD_PARTNER_PUBLIC_ID", ACME_ID)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("health must not be called without credentials")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    with pytest.raises(PreflightError, match="ADMIN_BOOTSTRAP_TOKEN"):
        run_smoke_preflight(host="http://test", client=client)


def test_assert_minimum_requests_rejects_zero() -> None:
    with pytest.raises(PreflightError, match="0 HTTP request"):
        assert_minimum_requests(request_count=0)


def test_assert_minimum_requests_accepts_positive() -> None:
    assert_minimum_requests(request_count=3, minimum=1)


def test_partner_slug_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_PARTNER_SLUG", raising=False)
    assert partner_slug() == "acme-erp"


def test_load_wait_bounds_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOAD_WAIT_MIN", raising=False)
    monkeypatch.delenv("LOAD_WAIT_MAX", raising=False)
    assert load_wait_bounds() == (0.1, 0.5)

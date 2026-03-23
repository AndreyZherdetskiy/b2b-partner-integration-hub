import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from app.observability.otel import (
    instrument_fastapi,
    instrument_httpx,
    otlp_http_signal_url,
    shutdown_otel,
)


def test_otlp_http_signal_url_appends_v1_path() -> None:
    assert (
        otlp_http_signal_url("http://otel-collector:4318", "traces")
        == "http://otel-collector:4318/v1/traces"
    )
    assert (
        otlp_http_signal_url("http://otel-collector:4318/", "metrics")
        == "http://otel-collector:4318/v1/metrics"
    )


def test_otlp_http_signal_url_idempotent_when_path_present() -> None:
    traces = "http://otel-collector:4318/v1/traces"
    assert otlp_http_signal_url(traces, "traces") == traces


def test_instrument_fastapi_noop_when_sdk_disabled() -> None:
    shutdown_otel()
    settings = Settings(_env_file=None, otel_sdk_disabled=True)
    app = create_app()
    instrument_fastapi(app, settings)


def test_instrument_httpx_noop_when_sdk_disabled() -> None:
    shutdown_otel()
    settings = Settings(_env_file=None, otel_sdk_disabled=True)
    instrument_httpx(settings)


def test_create_app_serves_health_when_sdk_disabled() -> None:
    shutdown_otel()
    get_settings.cache_clear()
    with patch.dict(os.environ, {"OTEL_SDK_DISABLED": "true"}, clear=False):
        get_settings.cache_clear()
        client = TestClient(create_app())
        response = client.get("/inbound/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_allows_local_admin_ui_origins() -> None:
    shutdown_otel()
    get_settings.cache_clear()
    with patch.dict(os.environ, {"OTEL_SDK_DISABLED": "true"}, clear=False):
        get_settings.cache_clear()
        client = TestClient(create_app())
        for origin in ("http://localhost:8080", "http://127.0.0.1:8080"):
            response = client.options(
                "/inbound/v1/health",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert response.headers.get("access-control-allow-origin") == origin

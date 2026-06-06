import json
import logging
from io import StringIO

import pytest
import structlog

from app.config import Settings
from app.logging import (
    bind_correlation_id,
    clear_correlation_id,
    configure_logging,
    reset_logging_for_tests,
)


@pytest.fixture(autouse=True)
def _logging_isolation() -> None:
    reset_logging_for_tests()
    yield
    reset_logging_for_tests()


def _capture_json_log(**event_fields: object) -> dict[str, object]:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    settings = Settings(_env_file=None, log_level="INFO")
    configure_logging(settings)

    log = structlog.get_logger("test")
    bind_correlation_id("0194a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b")
    try:
        log.info("test_event", **event_fields)
    finally:
        clear_correlation_id()

    handler.flush()
    line = stream.getvalue().strip().splitlines()[-1]
    return json.loads(line)


def test_redacts_authorization_header() -> None:
    payload = _capture_json_log(
        authorization="Bearer sk_live_secret_key_12345",
        message="inbound",
    )
    assert payload["authorization"] == "[REDACTED]"
    assert "sk_live" not in json.dumps(payload)


def test_redacts_x_hub_signature_256() -> None:
    payload = _capture_json_log(
        **{"x-hub-signature-256": "sha256=deadbeef", "x_hub_signature_256": "sha256=cafe"},
    )
    assert payload["x-hub-signature-256"] == "[REDACTED]"
    assert payload["x_hub_signature_256"] == "[REDACTED]"


def test_redacts_secret_fields() -> None:
    payload = _capture_json_log(
        api_key_secret="plain-secret",
        signing_secret="hmac-secret",
        fernet_key="fernet-key",
    )
    assert payload["api_key_secret"] == "[REDACTED]"
    assert payload["signing_secret"] == "[REDACTED]"
    assert payload["fernet_key"] == "[REDACTED]"


def test_includes_correlation_id_when_bound() -> None:
    payload = _capture_json_log(message="correlated")
    assert payload["correlation_id"] == "0194a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"


def test_includes_trace_and_span_ids_when_span_active() -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous = trace.get_tracer_provider()
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("test")

    try:
        with tracer.start_as_current_span("unit-test-span"):
            payload = _capture_json_log(traced="yes")
    finally:
        provider.shutdown()
        trace.set_tracer_provider(previous)

    assert payload["trace_id"]
    assert payload["span_id"]
    assert len(str(payload["trace_id"])) == 32
    assert len(str(payload["span_id"])) == 16


def test_json_log_has_required_fields() -> None:
    payload = _capture_json_log(
        event_type="order.created",
        delivery_id="0194a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c",
        partner_id="acme-corp",
        attempt=1,
        duration_ms=42,
        http_status=202,
        sla_breached=False,
    )
    assert payload["level"] == "info"
    assert payload["service"] == "hub-api"
    assert "timestamp" in payload
    assert payload["event_type"] == "order.created"
    assert payload["delivery_id"] == "0194a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c"
    assert payload["partner_id"] == "acme-corp"

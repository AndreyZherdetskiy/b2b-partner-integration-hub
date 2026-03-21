"""Unit tests for W3C traceparent propagation on Kafka headers."""

from __future__ import annotations

from contextlib import AbstractContextManager
from uuid import UUID

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import INVALID_SPAN_CONTEXT

from app.integrations.kafka_producer import _outbound_headers
from app.observability.trace_context import (
    attach_kafka_trace_context,
    detach_kafka_trace_context,
    kafka_trace_headers,
)
from app.workers.outbox_relay import _headers_from_payload


@pytest.fixture
def tracer_provider() -> AbstractContextManager[TracerProvider]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous = trace.get_tracer_provider()
    trace.set_tracer_provider(provider)
    try:
        yield provider
    finally:
        provider.shutdown()
        trace.set_tracer_provider(previous)


def _header_value(headers: list[tuple[str, bytes]], name: str) -> bytes | None:
    for key, value in headers:
        if key == name:
            return value
    return None


def test_kafka_trace_headers_includes_traceparent_when_span_active(
    tracer_provider: TracerProvider,
) -> None:
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("publish"):
        headers = kafka_trace_headers()

    traceparent = _header_value(headers, "traceparent")
    assert traceparent is not None
    assert traceparent.decode("utf-8").startswith("00-")


def test_kafka_trace_headers_empty_without_active_span() -> None:
    assert kafka_trace_headers() == []


def test_attach_kafka_trace_context_restores_remote_span_context(
    tracer_provider: TracerProvider,
) -> None:
    remote_trace_id = 0x4BF92F3577B34DA6A3CE929D0E0E4736
    remote_span_id = 0x00F067AA0BA902B7
    carrier_headers = [
        (
            "traceparent",
            f"00-{remote_trace_id:032x}-{remote_span_id:016x}-01".encode(),
        ),
    ]

    token = attach_kafka_trace_context(carrier_headers)
    try:
        current = trace.get_current_span().get_span_context()
        assert current.trace_id == remote_trace_id
        assert current.span_id == remote_span_id
        assert current.is_valid
    finally:
        detach_kafka_trace_context(token)

    assert trace.get_current_span().get_span_context() == INVALID_SPAN_CONTEXT


def test_outbound_headers_include_traceparent_with_correlation_id(
    tracer_provider: TracerProvider,
) -> None:
    delivery_id = UUID("0194a2b3-c4d5-7890-abcd-ef1234567890")
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("outbound-publish"):
        base = _outbound_headers(
            correlation_id="0194a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b",
            delivery_public_id=delivery_id,
            event_type="order.created",
            attempt=1,
        )
        headers = base + kafka_trace_headers()

    assert ("correlation_id", b"0194a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b") in headers
    traceparent = _header_value(headers, "traceparent")
    assert traceparent is not None
    assert traceparent.decode("utf-8").startswith("00-")


def test_outbox_relay_headers_include_traceparent(
    tracer_provider: TracerProvider,
) -> None:
    payload = {
        "correlation_id": "0194a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b",
        "event_type": "order.created",
        "delivery_id": "0194a2b3-c4d5-7890-abcd-ef1234567890",
    }
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("relay-publish"):
        headers = _headers_from_payload(payload)

    assert ("correlation_id", b"0194a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b") in headers
    traceparent = _header_value(headers, "traceparent")
    assert traceparent is not None
    assert traceparent.decode("utf-8").startswith("00-")

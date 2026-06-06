"""W3C Trace Context propagation for Kafka message headers."""

from __future__ import annotations

from collections.abc import Sequence

from opentelemetry import context as otel_context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_PROPAGATOR = TraceContextTextMapPropagator()
_W3C_HEADER_NAMES = frozenset({"traceparent", "tracestate"})


def kafka_trace_headers() -> list[tuple[str, bytes]]:
    """Inject current span context as Kafka headers (traceparent, optionally tracestate)."""
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    if not carrier:
        return []
    return [(name, value.encode("utf-8")) for name, value in carrier.items()]


def attach_kafka_trace_context(
    headers: Sequence[tuple[str, bytes]] | None,
) -> object:
    """Extract W3C context from Kafka headers and attach it to the current context."""
    if not headers:
        return otel_context.attach(otel_context.Context())

    carrier = {name: value.decode("utf-8") for name, value in headers if name in _W3C_HEADER_NAMES}
    if not carrier:
        return otel_context.attach(otel_context.Context())

    extracted = _PROPAGATOR.extract(carrier)
    return otel_context.attach(extracted)


def detach_kafka_trace_context(token: object) -> None:
    otel_context.detach(token)  # type: ignore[arg-type]

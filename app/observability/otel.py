"""OpenTelemetry SDK bootstrap — OTLP to Collector."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

if TYPE_CHECKING:
    from app.config import Settings

_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None


def build_resource(service_name: str, settings: Settings) -> Resource:
    return Resource.create(
        {
            "service.name": service_name,
            "service.version": settings.app_version,
            "deployment.environment": settings.deployment_environment,
        },
    )


def otlp_http_signal_url(base: str, signal: Literal["traces", "metrics"]) -> str:
    # HTTP exporter constructor takes the full signal path, not the Collector root.
    # https://opentelemetry.io/docs/languages/python/exporters/
    cleaned = base.rstrip("/")
    suffix = f"/v1/{signal}"
    if cleaned.endswith(suffix):
        return cleaned
    return f"{cleaned}{suffix}"


def configure_otel(service_name: str, settings: Settings) -> None:
    global _tracer_provider, _meter_provider

    if settings.otel_sdk_disabled:
        return

    resource = build_resource(service_name, settings)
    traces_url = otlp_http_signal_url(settings.otel_exporter_otlp_endpoint, "traces")
    metrics_url = otlp_http_signal_url(settings.otel_exporter_otlp_endpoint, "metrics")

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_url)),
    )
    trace.set_tracer_provider(tracer_provider)
    _tracer_provider = tracer_provider

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=metrics_url),
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    _meter_provider = meter_provider


def shutdown_otel() -> None:
    global _tracer_provider, _meter_provider

    if _tracer_provider is not None:
        _tracer_provider.shutdown()
        _tracer_provider = None

    if _meter_provider is not None:
        _meter_provider.shutdown()
        _meter_provider = None


def instrument_fastapi(app: FastAPI, settings: Settings) -> None:
    if settings.otel_sdk_disabled:
        return
    FastAPIInstrumentor.instrument_app(app)


def instrument_httpx(settings: Settings) -> None:
    if settings.otel_sdk_disabled:
        return
    HTTPXClientInstrumentor().instrument()

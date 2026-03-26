"""OTel metric instrument names and cardinality-safe attribute helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from opentelemetry import metrics

HUB_METRIC_NAMES: Final[tuple[str, ...]] = (
    "hub_deliveries_total",
    "hub_delivery_attempts_total",
    "hub_delivery_duration_seconds",
    "hub_dlq_messages_total",
    "hub_dlq_backlog",
    "hub_dlq_oldest_age_seconds",
    "hub_replay_total",
    "hub_circuit_breaker_state",
    "hub_inbound_events_total",
    "hub_inbound_duplicate_suppressed_total",
    "hub_rate_limit_rejected_total",
    "hub_sla_breaches_total",
    "hub_sla_compliance_ratio",
    "hub_outbox_unpublished",
    "hub_kafka_consumer_lag",
    "hub_invalid_transition_total",
)

FORBIDDEN_METRIC_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    {
        "delivery_id",
        "correlation_id",
        "trace_id",
        "partner_id",
    },
)


def validate_metric_attributes(attributes: Mapping[str, str]) -> dict[str, str]:
    for key in attributes:
        if key in FORBIDDEN_METRIC_ATTRIBUTES:
            msg = f"high-cardinality metric attribute forbidden: {key}"
            raise ValueError(msg)
    return dict(attributes)


def record_delivery_metric(
    metric_name: str,
    *,
    attributes: Mapping[str, str],
    value: int = 1,
) -> None:
    if metric_name not in HUB_METRIC_NAMES:
        msg = f"unknown metric name: {metric_name}"
        raise ValueError(msg)

    safe_attributes = validate_metric_attributes(attributes)
    meter = metrics.get_meter("partner_integration_hub.metrics")
    counter = meter.create_counter(metric_name)
    counter.add(value, safe_attributes)


def set_gauge_metric(
    metric_name: str,
    value: int,
    *,
    attributes: Mapping[str, str] | None = None,
) -> None:
    if metric_name not in HUB_METRIC_NAMES:
        msg = f"unknown metric name: {metric_name}"
        raise ValueError(msg)

    safe_attributes = validate_metric_attributes(attributes or {})
    meter = metrics.get_meter("partner_integration_hub.metrics")
    gauge = meter.create_gauge(metric_name)
    gauge.set(value, safe_attributes)

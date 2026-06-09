"""Retry topic tier selection (spec §6.6, ADR-002)."""

from __future__ import annotations

OUTBOUND_RETRY_30S_TOPIC = "hub.outbound.retry.30s"
OUTBOUND_RETRY_1M_TOPIC = "hub.outbound.retry.1m"
OUTBOUND_RETRY_5M_TOPIC = "hub.outbound.retry.5m"
OUTBOUND_RETRY_15M_TOPIC = "hub.outbound.retry.15m"
OUTBOUND_RETRY_1H_TOPIC = "hub.outbound.retry.1h"

OUTBOUND_RETRY_TOPICS: tuple[str, ...] = (
    OUTBOUND_RETRY_30S_TOPIC,
    OUTBOUND_RETRY_1M_TOPIC,
    OUTBOUND_RETRY_5M_TOPIC,
    OUTBOUND_RETRY_15M_TOPIC,
    OUTBOUND_RETRY_1H_TOPIC,
)


def retry_topic_for(attempt_number: int, delay_seconds: float) -> str:
    if attempt_number <= 2:
        return OUTBOUND_RETRY_30S_TOPIC
    if attempt_number == 3 and 45 < delay_seconds <= 90:
        return OUTBOUND_RETRY_1M_TOPIC
    if attempt_number <= 4:
        return OUTBOUND_RETRY_5M_TOPIC
    if attempt_number <= 6:
        return OUTBOUND_RETRY_15M_TOPIC
    return OUTBOUND_RETRY_1H_TOPIC

"""Unit tests for retry topic tier mapping (spec §6.6, Stage 2 Task 1)."""

from __future__ import annotations

import pytest

from app.domain.services.retry_topics import (
    OUTBOUND_RETRY_1H_TOPIC,
    OUTBOUND_RETRY_1M_TOPIC,
    OUTBOUND_RETRY_5M_TOPIC,
    OUTBOUND_RETRY_15M_TOPIC,
    OUTBOUND_RETRY_30S_TOPIC,
    retry_topic_for,
)


@pytest.mark.parametrize(
    ("attempt_number", "delay_seconds", "expected_topic"),
    [
        (2, 30.0, OUTBOUND_RETRY_30S_TOPIC),
        (2, 60.0, OUTBOUND_RETRY_30S_TOPIC),
        (3, 60.0, OUTBOUND_RETRY_1M_TOPIC),
        (3, 120.0, OUTBOUND_RETRY_5M_TOPIC),
        (4, 60.0, OUTBOUND_RETRY_5M_TOPIC),
        (4, 300.0, OUTBOUND_RETRY_5M_TOPIC),
        (5, 60.0, OUTBOUND_RETRY_15M_TOPIC),
        (6, 900.0, OUTBOUND_RETRY_15M_TOPIC),
        (8, 3600.0, OUTBOUND_RETRY_1H_TOPIC),
        (7, 30.0, OUTBOUND_RETRY_1H_TOPIC),
    ],
)
def test_retry_topic_for_mapping_table(
    attempt_number: int,
    delay_seconds: float,
    expected_topic: str,
) -> None:
    assert retry_topic_for(attempt_number, delay_seconds) == expected_topic


def test_attempt_3_prefers_5m_over_1m_delay_tier() -> None:
    assert retry_topic_for(3, 120.0) == OUTBOUND_RETRY_5M_TOPIC


def test_attempt_2_stays_on_30s_even_with_longer_delay() -> None:
    assert retry_topic_for(2, 120.0) == OUTBOUND_RETRY_30S_TOPIC

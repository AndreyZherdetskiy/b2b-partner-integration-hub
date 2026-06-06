"""Unit tests for outbound consumer scheduled_at handling."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiokafka import TopicPartition

from app.integrations.kafka_producer import OUTBOUND_PENDING_TOPIC, OUTBOUND_RETRY_30S_TOPIC
from app.workers.outbound_consumer import OutboundConsumer, wait_until_scheduled

FIXED_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_wait_until_scheduled_no_op_when_past() -> None:
    scheduled = (FIXED_NOW - timedelta(seconds=10)).isoformat()
    sleep = AsyncMock()
    await wait_until_scheduled(
        scheduled,
        now=lambda: FIXED_NOW,
        sleep=sleep,
    )
    sleep.assert_not_called()


@pytest.mark.asyncio
async def test_wait_until_scheduled_sleeps_in_chunks() -> None:
    scheduled = (FIXED_NOW + timedelta(seconds=12)).isoformat()
    now = FIXED_NOW

    async def advancing_sleep(seconds: float) -> None:
        nonlocal now
        now += timedelta(seconds=seconds)

    sleep = AsyncMock(side_effect=advancing_sleep)
    await wait_until_scheduled(
        scheduled,
        now=lambda: now,
        sleep=sleep,
    )
    assert sleep.await_count == 3
    sleep.assert_any_await(5.0)
    sleep.assert_any_await(2.0)


@pytest.mark.asyncio
async def test_wait_until_scheduled_stops_on_shutdown() -> None:
    scheduled = (FIXED_NOW + timedelta(seconds=30)).isoformat()
    shutdown = asyncio.Event()
    shutdown.set()
    sleep = AsyncMock()
    await wait_until_scheduled(
        scheduled,
        now=lambda: FIXED_NOW,
        sleep=sleep,
        shutdown=shutdown,
    )
    sleep.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_pending_does_not_wait() -> None:
    consumer = OutboundConsumer()
    envelope = {"scheduled_at": (FIXED_NOW + timedelta(hours=1)).isoformat()}
    message = MagicMock()
    message.topic = OUTBOUND_PENDING_TOPIC
    message.partition = 0
    message.value = envelope
    message.offset = 7
    kafka_consumer = MagicMock()
    kafka_consumer.commit = AsyncMock()
    session = MagicMock()
    sessionmaker = MagicMock()
    sessionmaker.return_value.__aenter__ = AsyncMock(return_value=session)
    sessionmaker.return_value.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "app.workers.outbound_consumer.wait_until_scheduled",
            new=AsyncMock(),
        ) as wait_mock,
        patch(
            "app.workers.outbound_consumer.process_outbound_message",
            new=AsyncMock(),
        ),
    ):
        await consumer._handle_message(
            sessionmaker,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            kafka_consumer,
            message,
        )

    wait_mock.assert_not_called()
    kafka_consumer.commit.assert_awaited_once_with({TopicPartition(OUTBOUND_PENDING_TOPIC, 0): 8})


@pytest.mark.asyncio
async def test_handle_message_retry_waits_until_scheduled() -> None:
    consumer = OutboundConsumer()
    scheduled = (FIXED_NOW + timedelta(seconds=30)).isoformat()
    envelope = {"scheduled_at": scheduled}
    message = MagicMock()
    message.topic = OUTBOUND_RETRY_30S_TOPIC
    message.partition = 2
    message.value = envelope
    message.offset = 3
    kafka_consumer = MagicMock()
    kafka_consumer.commit = AsyncMock()
    session = MagicMock()
    sessionmaker = MagicMock()
    sessionmaker.return_value.__aenter__ = AsyncMock(return_value=session)
    sessionmaker.return_value.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "app.workers.outbound_consumer.wait_until_scheduled",
            new=AsyncMock(),
        ) as wait_mock,
        patch(
            "app.workers.outbound_consumer.process_outbound_message",
            new=AsyncMock(),
        ),
    ):
        await consumer._handle_message(
            sessionmaker,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            kafka_consumer,
            message,
        )

    wait_mock.assert_awaited_once_with(scheduled, shutdown=consumer._shutdown)
    kafka_consumer.commit.assert_awaited_once_with(
        {TopicPartition(OUTBOUND_RETRY_30S_TOPIC, 2): 4}
    )

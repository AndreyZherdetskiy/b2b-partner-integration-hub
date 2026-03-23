"""Kafka consumer for outbound pending and retry topics."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from aiokafka import AIOKafkaConsumer, TopicPartition

from app.config import get_settings
from app.db.session import get_sessionmaker
from app.domain.services.retry_topics import OUTBOUND_RETRY_TOPICS
from app.integrations.kafka_producer import (
    OUTBOUND_PENDING_TOPIC,
    create_kafka_producer,
)
from app.integrations.redis_client import close_redis_pool, create_redis_client, create_redis_pool
from app.logging import configure_logging
from app.observability.otel import configure_otel, instrument_httpx, shutdown_otel
from app.observability.trace_context import (
    attach_kafka_trace_context,
    detach_kafka_trace_context,
)
from app.workers.outbound_processor import process_outbound_message

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "hub-outbound-worker"
CONSUME_TOPICS = (OUTBOUND_PENDING_TOPIC, *OUTBOUND_RETRY_TOPICS)
GRACEFUL_SHUTDOWN_SECONDS = 30
_SCHEDULED_SLEEP_CHUNK_SECONDS = 5.0


async def wait_until_scheduled(
    scheduled_at_iso: str,
    *,
    now: Callable[[], datetime] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    shutdown: asyncio.Event | None = None,
) -> None:
    scheduled_at = datetime.fromisoformat(scheduled_at_iso)
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=UTC)

    current_time = now or (lambda: datetime.now(UTC))
    sleep_fn = sleep or asyncio.sleep

    while True:
        if shutdown is not None and shutdown.is_set():
            return

        remaining = (scheduled_at - current_time()).total_seconds()
        if remaining <= 0:
            return

        await sleep_fn(min(remaining, _SCHEDULED_SLEEP_CHUNK_SECONDS))


class OutboundConsumer:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._shutdown = asyncio.Event()
        self._in_flight: asyncio.Task[None] | None = None

    async def run(self) -> None:
        configure_logging(self._settings)
        configure_otel("hub-outbound-worker", self._settings)
        instrument_httpx(self._settings)

        sessionmaker = get_sessionmaker(self._settings)
        producer = create_kafka_producer(self._settings)
        await producer.start()
        redis_pool = create_redis_pool(self._settings)
        redis = create_redis_client(redis_pool)

        consumer = AIOKafkaConsumer(
            *CONSUME_TOPICS,
            bootstrap_servers=self._settings.kafka_bootstrap_servers,
            group_id=CONSUMER_GROUP,
            enable_auto_commit=False,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        )
        await consumer.start()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._request_shutdown)

        http_client = httpx.AsyncClient()

        try:
            while not self._shutdown.is_set():
                try:
                    batch = await asyncio.wait_for(
                        consumer.getmany(timeout_ms=500, max_records=10),
                        timeout=1.0,
                    )
                except TimeoutError:
                    continue

                if not batch:
                    continue

                for _tp, messages in batch.items():
                    for message in messages:
                        if self._shutdown.is_set():
                            break
                        self._in_flight = asyncio.create_task(
                            self._handle_message(
                                sessionmaker,
                                producer,
                                http_client,
                                redis,
                                consumer,
                                message,
                            )
                        )
                        await self._in_flight
                        self._in_flight = None

            if self._in_flight is not None:
                await asyncio.wait_for(self._in_flight, timeout=GRACEFUL_SHUTDOWN_SECONDS)
        finally:
            await http_client.aclose()
            await consumer.stop()
            await producer.stop()
            await close_redis_pool(redis_pool)
            shutdown_otel()

    def _request_shutdown(self) -> None:
        logger.info("shutdown_requested")
        self._shutdown.set()

    async def _handle_message(
        self,
        sessionmaker: Any,
        producer: Any,
        http_client: httpx.AsyncClient,
        redis: Any,
        consumer: AIOKafkaConsumer,
        message: Any,
    ) -> None:
        envelope = message.value
        if message.topic != OUTBOUND_PENDING_TOPIC:
            await wait_until_scheduled(
                envelope["scheduled_at"],
                shutdown=self._shutdown,
            )
            if self._shutdown.is_set():
                return

        token = attach_kafka_trace_context(message.headers)
        try:
            async with sessionmaker() as session:
                await process_outbound_message(
                    session,
                    producer,
                    envelope,
                    self._settings,
                    http_client=http_client,
                    redis=redis,
                )
        finally:
            detach_kafka_trace_context(token)
        # ConsumerRecord has topic/partition; topic_partition exists on RecordMetadata only.
        # https://aiokafka.readthedocs.io/en/stable/api.html#aiokafka.AIOKafkaConsumer.commit
        tp = TopicPartition(message.topic, message.partition)
        await consumer.commit({tp: message.offset + 1})


async def _main() -> None:
    consumer = OutboundConsumer()
    await consumer.run()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()

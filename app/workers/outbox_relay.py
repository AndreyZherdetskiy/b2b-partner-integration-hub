"""Transactional outbox relay: publish unpublished rows to Kafka."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from aiokafka import AIOKafkaProducer
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_sessionmaker
from app.domain.models.inbound_event import InboundEvent
from app.domain.models.outbox import OutboxEvent
from app.integrations.kafka_producer import create_kafka_producer
from app.logging import configure_logging
from app.observability.metrics import set_gauge_metric
from app.observability.otel import configure_otel, shutdown_otel
from app.observability.trace_context import kafka_trace_headers

logger = logging.getLogger(__name__)

EMPTY_BATCH_SLEEP_SECONDS = 1.0
_SHUTDOWN_POLL_CHUNK_SECONDS = 0.5


def unpublished_outbox_select(*, limit: int) -> Any:
    return (
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None))
        .order_by(OutboxEvent.created_at)
        .with_for_update(skip_locked=True)
        .limit(limit)
    )


def _headers_from_payload(payload: dict[str, object]) -> list[tuple[str, bytes]]:
    headers: list[tuple[str, bytes]] = []
    correlation_id = payload.get("correlation_id")
    if correlation_id is not None:
        headers.append(("correlation_id", str(correlation_id).encode("utf-8")))
    event_type = payload.get("event_type")
    if event_type is not None:
        headers.append(("event_type", str(event_type).encode("utf-8")))
    delivery_id = payload.get("delivery_id")
    if delivery_id is not None:
        headers.append(("delivery_id", str(delivery_id).encode("utf-8")))
    if headers:
        headers.append(("content-type", b"application/json"))
    return headers + kafka_trace_headers()


async def _count_unpublished(session: AsyncSession) -> int:
    stmt = select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published_at.is_(None))
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def _mark_inbound_published(
    session: AsyncSession,
    payload: dict[str, object],
    published_at: datetime,
) -> None:
    event_id = UUID(str(payload["event_id"]))
    await session.execute(
        update(InboundEvent).where(InboundEvent.id == event_id).values(published_at=published_at),
    )


async def publish_unpublished_batch(
    session: AsyncSession,
    producer: AIOKafkaProducer,
    *,
    limit: int = 100,
) -> int:
    result = await session.execute(unpublished_outbox_select(limit=limit))
    events = list(result.scalars().all())
    published_count = 0

    for event in events:
        try:
            await producer.send_and_wait(
                event.topic,
                key=event.message_key,
                value=event.payload,
                headers=_headers_from_payload(event.payload),
            )
        except Exception:
            event.publish_attempts += 1
            logger.exception(
                "outbox_publish_failed",
                extra={"outbox_id": event.id, "topic": event.topic},
            )
            continue

        published_at = datetime.now(UTC)
        event.published_at = published_at
        if event.aggregate_type == "inbound_event":
            await _mark_inbound_published(session, event.payload, published_at)
        published_count += 1

    unpublished_count = await _count_unpublished(session)
    set_gauge_metric("hub_outbox_unpublished", unpublished_count)
    return published_count


class OutboxRelay:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._shutdown = asyncio.Event()

    async def run(self) -> None:
        configure_logging(self._settings)
        configure_otel("hub-outbox-relay", self._settings)

        sessionmaker = get_sessionmaker(self._settings)
        producer = create_kafka_producer(self._settings)
        await producer.start()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._request_shutdown)

        try:
            while not self._shutdown.is_set():
                async with sessionmaker() as session:
                    published = await publish_unpublished_batch(session, producer)
                    await session.commit()

                if published == 0:
                    await self._sleep_until_shutdown(EMPTY_BATCH_SLEEP_SECONDS)
        finally:
            await producer.stop()
            shutdown_otel()

    def _request_shutdown(self) -> None:
        logger.info("shutdown_requested")
        self._shutdown.set()

    async def _sleep_until_shutdown(self, total_seconds: float) -> None:
        remaining = total_seconds
        while remaining > 0 and not self._shutdown.is_set():
            chunk = min(remaining, _SHUTDOWN_POLL_CHUNK_SECONDS)
            await asyncio.sleep(chunk)
            remaining -= chunk


async def _main() -> None:
    relay = OutboxRelay()
    await relay.run()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()

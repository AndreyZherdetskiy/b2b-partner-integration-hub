"""Thin Kafka helpers for integration tests."""

from __future__ import annotations

import json
from typing import Any

from aiokafka import AIOKafkaConsumer

KAFKA_BOOTSTRAP = "localhost:9092"


class RecordingKafkaProducer:
    """Captures published messages without a live broker."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str | None, dict[str, Any]]] = []

    async def send_and_wait(
        self,
        topic: str,
        *,
        key: str | None = None,
        value: dict[str, Any] | None = None,
        headers: list[tuple[str, bytes]] | None = None,
    ) -> object:
        del headers
        self.messages.append((topic, key, value or {}))
        return None

    def topics(self) -> set[str]:
        return {topic for topic, _, _ in self.messages}

    def values_on(self, topic: str) -> list[dict[str, Any]]:
        return [value for t, _, value in self.messages if t == topic]


async def consume_messages(
    topic: str,
    *,
    predicate: Any,
    timeout_s: float = 15.0,
    bootstrap: str = KAFKA_BOOTSTRAP,
) -> list[dict[str, Any]]:
    """Collect Kafka messages matching predicate until timeout."""
    import time

    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=bootstrap,
        auto_offset_reset="latest",
        enable_auto_commit=True,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )
    await consumer.start()
    seen: list[dict[str, Any]] = []
    deadline = time.time() + timeout_s
    try:
        while time.time() < deadline:
            batch = await consumer.getmany(timeout_ms=1000)
            for _tp, messages in batch.items():
                for msg in messages:
                    if predicate(msg.value):
                        seen.append(msg.value)
            if seen:
                break
    finally:
        await consumer.stop()
    return seen

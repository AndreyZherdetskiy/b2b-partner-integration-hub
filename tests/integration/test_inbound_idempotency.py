"""Integration tests for inbound idempotency with PostgreSQL, Redis, and Kafka."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import pytest
from aiokafka import AIOKafkaConsumer
from fastapi.testclient import TestClient
from tests.integration.conftest import INTEGRATION_ADMIN_TOKEN, auth_header

from app.domain.services.hmac_service import sign

pytestmark = pytest.mark.integration

KAFKA_BOOTSTRAP = "localhost:9092"
TOPIC = "hub.inbound.order.created"


def _seed_partner(client: TestClient) -> tuple[dict[str, Any], str, str]:
    created = client.post(
        "/admin/v1/partners",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
        json={"slug": "inbound-idem", "name": "Inbound Idem", "sla_seconds": 60},
    )
    assert created.status_code == 201, created.text
    partner = created.json()
    client.patch(
        f"/admin/v1/partners/{partner['id']}",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
        json={"status": "active"},
    )
    key_res = client.post(
        f"/admin/v1/partners/{partner['id']}/api-keys",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
        json={"scopes": ["inbound:write"]},
    )
    assert key_res.status_code == 201, key_res.text
    api_key = key_res.json()["key"]
    signing_secret = partner["signing_secret"]
    return partner, api_key, signing_secret


def _signed_post(
    client: TestClient,
    *,
    slug: str,
    body: bytes,
    api_key: str,
    signing_secret: str,
    idempotency_key: str,
    timestamp: str | None = None,
) -> Any:
    ts = timestamp or str(int(time.time()))
    signature = sign(signing_secret, ts, body)
    return client.post(
        f"/inbound/v1/{slug}/events",
        content=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "X-Hub-Signature-256": signature,
            "X-Hub-Timestamp": ts,
            "Idempotency-Key": idempotency_key,
            "Content-Type": "application/json",
        },
    )


@pytest.mark.asyncio
async def test_idempotency_duplicate_suppresses_kafka(client: TestClient) -> None:
    partner, api_key, signing_secret = _seed_partner(client)
    body = b'{"event_type":"order.created","payload":{"order_id":"ord-int-1"}}'
    idem = f"idem-{uuid.uuid4()}"

    consumer = AIOKafkaConsumer(
        TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        auto_offset_reset="latest",
        enable_auto_commit=True,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )
    await consumer.start()
    try:
        first = _signed_post(
            client,
            slug=partner["slug"],
            body=body,
            api_key=api_key,
            signing_secret=signing_secret,
            idempotency_key=idem,
        )
        assert first.status_code == 202, first.text
        event_id = first.json()["event_id"]

        deadline = time.time() + 15
        seen: list[dict[str, Any]] = []
        while time.time() < deadline and len(seen) < 1:
            batch = await consumer.getmany(timeout_ms=1000)
            for _tp, messages in batch.items():
                for msg in messages:
                    if msg.value.get("idempotency_key") == idem:
                        seen.append(msg.value)

        assert len(seen) == 1
        assert seen[0]["event_id"] == event_id
        assert seen[0]["partner_id"] == partner["id"]

        second = _signed_post(
            client,
            slug=partner["slug"],
            body=body,
            api_key=api_key,
            signing_secret=signing_secret,
            idempotency_key=idem,
        )
        assert second.status_code == 200, second.text
        assert second.json()["status"] == "duplicate"
        assert second.json()["event_id"] == event_id

        extra_deadline = time.time() + 5
        while time.time() < extra_deadline:
            batch = await consumer.getmany(timeout_ms=500)
            for _tp, messages in batch.items():
                for msg in messages:
                    if msg.value.get("idempotency_key") == idem:
                        seen.append(msg.value)
            if len(seen) > 1:
                break

        assert len(seen) == 1
    finally:
        await consumer.stop()

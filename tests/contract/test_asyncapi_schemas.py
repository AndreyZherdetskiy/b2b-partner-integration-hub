"""Contract tests: AsyncAPI Stage 1 topics and envelope builders."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import yaml

from app.domain.services.retry_topics import (
    OUTBOUND_RETRY_1H_TOPIC,
    OUTBOUND_RETRY_1M_TOPIC,
    OUTBOUND_RETRY_5M_TOPIC,
    OUTBOUND_RETRY_15M_TOPIC,
)
from app.integrations.kafka_producer import (
    OUTBOUND_DLQ_TOPIC,
    OUTBOUND_PENDING_TOPIC,
    OUTBOUND_RETRY_30S_TOPIC,
    SLA_BREACHED_TOPIC,
    build_inbound_envelope,
    build_outbound_pending_envelope,
    inbound_topic,
)

ASYNCAPI_PATH = Path("docs/asyncapi/asyncapi.yaml")

STAGE1_TOPICS = frozenset(
    {
        OUTBOUND_PENDING_TOPIC,
        OUTBOUND_RETRY_30S_TOPIC,
        OUTBOUND_DLQ_TOPIC,
        inbound_topic("order.created"),
        inbound_topic("order.updated"),
        SLA_BREACHED_TOPIC,
    }
)

STAGE2_RETRY_TOPICS = frozenset(
    {
        OUTBOUND_RETRY_1M_TOPIC,
        OUTBOUND_RETRY_5M_TOPIC,
        OUTBOUND_RETRY_15M_TOPIC,
        OUTBOUND_RETRY_1H_TOPIC,
    }
)

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _load_asyncapi() -> dict:
    return yaml.safe_load(ASYNCAPI_PATH.read_text(encoding="utf-8"))


def _channel_addresses(doc: dict) -> set[str]:
    channels = doc.get("channels", {})
    addresses: set[str] = set()
    for channel in channels.values():
        address = channel.get("address")
        if address:
            addresses.add(address)
    return addresses


def _assert_uuid_string(value: object) -> None:
    assert isinstance(value, str), f"expected string id, got {type(value)}"
    assert UUID_RE.match(value), f"expected UUID string, got {value!r}"
    assert not value.isdigit(), "id must not be an integer string"


def test_asyncapi_stage1_topics_exist() -> None:
    doc = _load_asyncapi()
    addresses = _channel_addresses(doc)
    missing = STAGE1_TOPICS - addresses
    assert not missing, f"missing AsyncAPI channels for topics: {sorted(missing)}"


def test_asyncapi_stage2_retry_tier_addresses_exist() -> None:
    doc = _load_asyncapi()
    addresses = _channel_addresses(doc)
    missing = STAGE2_RETRY_TOPICS - addresses
    assert not missing, f"missing AsyncAPI retry-tier channels: {sorted(missing)}"


def test_asyncapi_kafka_key_on_message_not_channel() -> None:
    doc = _load_asyncapi()
    expected_message_keys = frozenset(
        {"OutboundPending", "OutboundDlq", "InboundEvent", "SlaBreached"}
    )

    for channel in doc.get("channels", {}).values():
        kafka_binding = channel.get("bindings", {}).get("kafka", {})
        assert "key" not in kafka_binding, (
            "channel kafka binding must not contain key; "
            "use message kafka binding per AsyncAPI Kafka bindings spec"
        )

    messages = doc.get("components", {}).get("messages", {})
    for name in expected_message_keys:
        assert name in messages, f"missing message component: {name}"
        kafka_binding = messages[name].get("bindings", {}).get("kafka", {})
        assert "key" in kafka_binding, f"message {name} must declare kafka key binding"
        key_schema = kafka_binding["key"]
        assert key_schema.get("type") == "string"
        assert key_schema.get("format") == "uuid"
        assert "partner public_id" in key_schema.get("description", "").lower()


def test_build_inbound_envelope_schema_version_and_uuid_ids() -> None:
    event_id = UUID("018f3c4a-5b6c-7890-abcd-ef1234567890")
    partner_id = UUID("018f3c4a-5b6c-7890-abcd-ef1234567891")
    envelope = build_inbound_envelope(
        event_id=event_id,
        partner_public_id=partner_id,
        event_type="order.created",
        payload={"order_id": "ord-1"},
        idempotency_key="idem-1",
        correlation_id="018f3c4a-5b6c-7890-abcd-ef1234567892",
        received_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
    )
    assert envelope["schema_version"] == 1
    _assert_uuid_string(envelope["event_id"])
    _assert_uuid_string(envelope["partner_id"])
    assert envelope["event_id"] == str(event_id)
    assert envelope["partner_id"] == str(partner_id)


def test_build_outbound_pending_envelope_schema_version_and_uuid_ids() -> None:
    delivery_id = UUID("018f3c4a-5b6c-7890-abcd-ef1234567893")
    partner_id = UUID("018f3c4a-5b6c-7890-abcd-ef1234567894")
    endpoint_id = UUID("018f3c4a-5b6c-7890-abcd-ef1234567895")
    envelope = build_outbound_pending_envelope(
        delivery_public_id=delivery_id,
        partner_public_id=partner_id,
        endpoint_id=endpoint_id,
        event_type="order.created",
        payload={"order_id": "ord-1"},
        idempotency_key="idem-out-1",
        correlation_id="018f3c4a-5b6c-7890-abcd-ef1234567896",
        scheduled_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
        sla_deadline_at=datetime(2026, 6, 1, 12, 1, 0, tzinfo=UTC),
        attempt=1,
    )
    assert envelope["schema_version"] == 1
    _assert_uuid_string(envelope["delivery_id"])
    _assert_uuid_string(envelope["partner_id"])
    _assert_uuid_string(envelope["endpoint_id"])
    assert envelope["delivery_id"] == str(delivery_id)
    assert envelope["partner_id"] == str(partner_id)
    assert envelope["endpoint_id"] == str(endpoint_id)

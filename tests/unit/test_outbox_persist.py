"""Single-statement persist of deliveries + outbox (accept-path round-trip)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from app.domain.enums import DeliveryDirection, DeliveryStatus
from app.domain.ids import generate_uuidv7
from app.domain.models.delivery import Delivery
from app.domain.services.outbox import build_delivery_outbox_persist_stmt
from app.integrations.kafka_producer import OUTBOUND_PENDING_TOPIC, build_outbound_pending_envelope


def _delivery() -> Delivery:
    public_id = generate_uuidv7()
    return Delivery(
        public_id=public_id,
        partner_id=1,
        endpoint_id=generate_uuidv7(),
        direction=DeliveryDirection.OUTBOUND,
        event_type="order.created",
        idempotency_key="idem-1::ep",
        source_event_id="idem-1",
        payload={"order_id": "ord_1"},
        payload_hash="abc",
        status=DeliveryStatus.PENDING,
        attempt_count=0,
        max_attempts=6,
        sla_deadline_at=datetime.now(UTC) + timedelta(seconds=90),
        sla_breached=False,
        correlation_id=str(generate_uuidv7()),
    )


def test_persist_stmt_is_one_data_modifying_cte() -> None:
    delivery = _delivery()
    now = datetime.now(UTC)
    envelope = build_outbound_pending_envelope(
        delivery_public_id=delivery.public_id,
        partner_public_id=generate_uuidv7(),
        endpoint_id=delivery.endpoint_id,
        event_type=delivery.event_type,
        payload=delivery.payload,
        idempotency_key=delivery.source_event_id or "",
        correlation_id=delivery.correlation_id,
        scheduled_at=now,
        sla_deadline_at=delivery.sla_deadline_at,
        attempt=1,
    )
    stmt = build_delivery_outbox_persist_stmt(
        deliveries=[delivery],
        envelopes=[envelope],
        message_key="0194a2b3-c4d5-7890-abcd-ef1234567890",
        topic=OUTBOUND_PENDING_TOPIC,
    )
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled).lower()
    assert "with" in sql
    assert "insert into deliveries" in sql.replace('"', "")
    assert "outbox_events" in sql
    assert "ins_deliveries.id" in sql.replace('"', "")
    assert "publish_attempts" not in sql
    assert sql.count("insert into") >= 2
    assert ";" not in sql.strip().rstrip(";")
    assert compiled.params.get("publish_attempts") is None


def test_persist_stmt_rejects_mismatched_envelope_count() -> None:
    delivery = _delivery()
    with pytest.raises(ValueError, match="envelopes"):
        build_delivery_outbox_persist_stmt(
            deliveries=[delivery],
            envelopes=[],
            message_key="k",
            topic=OUTBOUND_PENDING_TOPIC,
        )

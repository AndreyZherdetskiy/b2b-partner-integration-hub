"""Unit tests for Celery beat tasks (maintenance only — not webhook transport)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from celery.schedules import schedule as celery_schedule

from app.domain.enums import DeliveryStatus, PartnerStatus
from app.domain.ids import generate_uuidv7
from app.domain.models.delivery import Delivery
from app.domain.models.partner import Partner
from app.domain.services.circuit_breaker import CircuitState

BEAT_TASK_KEYS = (
    "replay_stale_failed",
    "purge_old_idempotency_keys",
    "rotate_webhook_secrets",
)


def _partner(*, slug: str, auto_replay: bool) -> Partner:
    return Partner(
        id=hash(slug) % 1000 + 1,
        public_id=generate_uuidv7(),
        slug=slug,
        name=slug,
        status=PartnerStatus.ACTIVE,
        sla_seconds=120,
        auto_replay_enabled=auto_replay,
        rate_limit_rps=100,
        signing_secret_encrypted=None,
    )


def _failed_delivery(*, partner: Partner, age_hours: float = 2.0) -> Delivery:
    return Delivery(
        id=100 + hash(partner.slug) % 100,
        public_id=generate_uuidv7(),
        partner_id=partner.id,
        endpoint_id=generate_uuidv7(),
        direction="outbound",
        event_type="order.created",
        idempotency_key=f"idem-{partner.slug}",
        payload={"order_id": partner.slug},
        payload_hash=f"hash-{partner.slug}",
        status=DeliveryStatus.FAILED,
        attempt_count=2,
        max_attempts=6,
        sla_deadline_at=datetime.now(UTC) + timedelta(seconds=90),
        correlation_id=str(generate_uuidv7()),
        updated_at=datetime.now(UTC) - timedelta(hours=age_hours),
    )


@pytest.mark.parametrize("key", BEAT_TASK_KEYS)
def test_beat_schedule_defines_task(key: str) -> None:
    from celery_app.tasks.beat_schedule import BEAT_SCHEDULE

    assert key in BEAT_SCHEDULE


def test_stale_replay_schedule_is_six_hours() -> None:
    from celery_app.tasks.beat_schedule import BEAT_SCHEDULE

    sched = BEAT_SCHEDULE["replay_stale_failed"]["schedule"]
    if isinstance(sched, celery_schedule):
        assert sched.run_every == 6 * 3600
    else:
        assert sched == timedelta(hours=6)


@pytest.mark.asyncio
async def test_stale_replay_skips_auto_replay_disabled() -> None:
    from celery_app.tasks.replay import replay_stale_failed_deliveries

    partner = _partner(slug="no-auto", auto_replay=False)
    delivery = _failed_delivery(partner=partner)
    session = AsyncMock()

    with (
        patch(
            "celery_app.tasks.replay._fetch_stale_failed_deliveries",
            new_callable=AsyncMock,
            return_value=[(delivery, partner)],
        ),
        patch(
            "celery_app.tasks.replay.replay_delivery",
            new_callable=AsyncMock,
        ) as mock_replay,
    ):
        result = await replay_stale_failed_deliveries(session, redis=None, settings=MagicMock())

    assert result.replayed == 0
    mock_replay.assert_not_called()


@pytest.mark.asyncio
async def test_stale_replay_skips_open_circuit() -> None:
    from celery_app.tasks.replay import replay_stale_failed_deliveries

    partner = _partner(slug="open-cb", auto_replay=True)
    delivery = _failed_delivery(partner=partner)
    session = AsyncMock()

    with (
        patch(
            "celery_app.tasks.replay._fetch_stale_failed_deliveries",
            new_callable=AsyncMock,
            return_value=[(delivery, partner)],
        ),
        patch(
            "celery_app.tasks.replay.get_circuit_state",
            new_callable=AsyncMock,
            return_value=CircuitState.OPEN,
        ),
        patch(
            "celery_app.tasks.replay.replay_delivery",
            new_callable=AsyncMock,
        ) as mock_replay,
    ):
        result = await replay_stale_failed_deliveries(session, redis=None, settings=MagicMock())

    assert result.skipped_open_circuit == 1
    mock_replay.assert_not_called()


@pytest.mark.asyncio
async def test_stale_replay_skips_half_open_circuit() -> None:
    from celery_app.tasks.replay import replay_stale_failed_deliveries

    partner = _partner(slug="half-open", auto_replay=True)
    delivery = _failed_delivery(partner=partner)
    session = AsyncMock()

    with (
        patch(
            "celery_app.tasks.replay._fetch_stale_failed_deliveries",
            new_callable=AsyncMock,
            return_value=[(delivery, partner)],
        ),
        patch(
            "celery_app.tasks.replay.get_circuit_state",
            new_callable=AsyncMock,
            return_value=CircuitState.HALF_OPEN,
        ),
        patch(
            "celery_app.tasks.replay.replay_delivery",
            new_callable=AsyncMock,
        ) as mock_replay,
    ):
        result = await replay_stale_failed_deliveries(session, redis=None, settings=MagicMock())

    assert result.skipped_open_circuit == 1
    mock_replay.assert_not_called()


@pytest.mark.asyncio
async def test_stale_replay_replays_closed_circuit_with_scheduled_trigger() -> None:
    from celery_app.tasks.replay import replay_stale_failed_deliveries

    partner = _partner(slug="replay-me", auto_replay=True)
    delivery = _failed_delivery(partner=partner)
    session = AsyncMock()

    with (
        patch(
            "celery_app.tasks.replay._fetch_stale_failed_deliveries",
            new_callable=AsyncMock,
            return_value=[(delivery, partner)],
        ),
        patch(
            "celery_app.tasks.replay.get_circuit_state",
            new_callable=AsyncMock,
            return_value=CircuitState.CLOSED,
        ),
        patch(
            "celery_app.tasks.replay.allow_request",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "celery_app.tasks.replay.replay_delivery",
            new_callable=AsyncMock,
        ) as mock_replay,
    ):
        result = await replay_stale_failed_deliveries(session, redis=None, settings=MagicMock())

    assert result.replayed == 1
    mock_replay.assert_awaited_once_with(
        session,
        delivery_public_id=delivery.public_id,
        actor_id="celery-beat",
        reason="scheduled stale failed replay",
        reset_attempt_counter=False,
        trigger="scheduled",
    )


@pytest.mark.asyncio
async def test_replay_delivery_scheduled_trigger_records_metric() -> None:
    from app.domain.services.replay_service import replay_delivery

    partner = _partner(slug="metric-partner", auto_replay=True)
    delivery = _failed_delivery(partner=partner)
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with (
        patch(
            "app.domain.services.replay_service.fetch_delivery_with_partner",
            new_callable=AsyncMock,
            return_value=(delivery, partner),
        ),
        patch(
            "app.domain.services.replay_service.record_delivery_metric",
        ) as mock_metric,
        patch(
            "app.domain.services.replay_service.enqueue_outbox",
        ),
    ):
        await replay_delivery(
            session,
            delivery_public_id=delivery.public_id,
            actor_id="celery-beat",
            reason="scheduled",
            reset_attempt_counter=False,
            trigger="scheduled",
        )

    mock_metric.assert_called_once_with(
        "hub_replay_total",
        attributes={"trigger": "scheduled", "partner_slug": partner.slug},
    )


async def test_rotate_webhook_secrets_does_not_mutate_signing_secrets() -> None:
    from celery_app.tasks.secret_rotation import notify_secret_rotation_due

    partner = _partner(slug="static-secret", auto_replay=False)
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [partner]
    session.execute = AsyncMock(return_value=result)

    with patch(
        "app.domain.services.signing_secrets.rotate_partner_signing_secret",
        new_callable=AsyncMock,
    ) as mock_rotate:
        await notify_secret_rotation_due(session, settings=MagicMock())

    mock_rotate.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_rotate_webhook_secrets_notify_only() -> None:
    from celery_app.tasks.secret_rotation import notify_secret_rotation_due

    partner = _partner(slug="notify-only", auto_replay=False)
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [partner]
    session.execute = AsyncMock(return_value=result)

    count = await notify_secret_rotation_due(session, settings=MagicMock())

    assert count == 1
    session.add.assert_not_called()

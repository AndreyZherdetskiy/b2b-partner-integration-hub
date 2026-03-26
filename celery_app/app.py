"""Celery application instance."""

from __future__ import annotations

from celery import Celery

from app.config import get_settings
from celery_app.tasks.beat_schedule import BEAT_SCHEDULE


def _create_celery() -> Celery:
    settings = get_settings()
    app = Celery(
        "partner_integration_hub",
        broker=settings.celery_broker_url,
        include=[
            "celery_app.tasks.replay",
            "celery_app.tasks.maintenance",
            "celery_app.tasks.secret_rotation",
        ],
    )
    app.conf.update(
        timezone="UTC",
        enable_utc=True,
        beat_schedule=BEAT_SCHEDULE,
        task_default_queue="hub-maintenance",
    )
    return app


celery = _create_celery()

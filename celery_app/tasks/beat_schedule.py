"""Celery beat schedule (code-defined per Celery 5.x)."""

from __future__ import annotations

from datetime import timedelta

from celery.schedules import crontab

BEAT_SCHEDULE = {
    "replay_stale_failed": {
        "task": "celery_app.tasks.replay.replay_stale_failed",
        "schedule": timedelta(hours=6),
    },
    "purge_old_idempotency_keys": {
        "task": "celery_app.tasks.maintenance.purge_old_idempotency_keys",
        "schedule": crontab(minute=0, hour=3),
    },
    "rotate_webhook_secrets": {
        "task": "celery_app.tasks.secret_rotation.rotate_webhook_secrets",
        "schedule": crontab(minute=0, hour=4, day_of_week="mon"),
    },
}

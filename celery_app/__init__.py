"""Celery application package (scheduled maintenance only — not webhook transport)."""

from celery_app.app import celery

__all__ = ["celery"]

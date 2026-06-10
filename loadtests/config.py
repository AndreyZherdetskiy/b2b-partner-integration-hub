"""Environment-backed defaults for load scenarios (process env only)."""

from __future__ import annotations

import os
from typing import Final

DEFAULT_LOAD_HOST: Final = "http://127.0.0.1:8000"
DEFAULT_PARTNER_SLUG: Final = "acme-erp"


def load_host() -> str:
    raw = os.environ.get("LOAD_HOST") or os.environ.get("BASE_URL") or DEFAULT_LOAD_HOST
    return raw.rstrip("/")


def admin_token() -> str:
    """Admin Bearer token from process env only (no pydantic `.env` file)."""
    return (
        os.environ.get("LOAD_ADMIN_TOKEN") or os.environ.get("ADMIN_BOOTSTRAP_TOKEN") or ""
    ).strip()


def partner_slug() -> str:
    return (os.environ.get("LOAD_PARTNER_SLUG") or DEFAULT_PARTNER_SLUG).strip()


def partner_public_id_from_env() -> str | None:
    value = os.environ.get("LOAD_PARTNER_PUBLIC_ID", "").strip()
    return value or None


def load_wait_bounds() -> tuple[float, float]:
    min_wait = float(os.environ.get("LOAD_WAIT_MIN", "0.1"))
    max_wait = float(os.environ.get("LOAD_WAIT_MAX", "0.5"))
    return min_wait, max_wait

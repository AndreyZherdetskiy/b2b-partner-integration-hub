"""E2E fixtures — require hub-api on localhost (see `make compose-up` + `make seed`)."""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path

import httpx
import pytest

HUB_BASE_URL = os.getenv("HUB_API_BASE", "http://127.0.0.1:8000").rstrip("/")
HUB_DOCS_URL = os.getenv("HUB_API_DOCS", f"{HUB_BASE_URL}/docs")
HUB_HEALTH_URL = os.getenv("HUB_API_HEALTH", f"{HUB_BASE_URL}/inbound/v1/health")
POLL_TIMEOUT_SECONDS = float(os.getenv("E2E_POLL_TIMEOUT_SECONDS", "30"))
POLL_INTERVAL_SECONDS = float(os.getenv("E2E_POLL_INTERVAL_SECONDS", "0.5"))


def _url_reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _resolve_admin_token() -> str | None:
    token = os.environ.get("ADMIN_BOOTSTRAP_TOKEN")
    if token:
        return token
    env_path = Path(".env")
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("ADMIN_BOOTSTRAP_TOKEN="):
            return stripped.split("=", 1)[1].strip()
    return None


@pytest.fixture(scope="session")
def hub_base_url() -> str:
    if not (_url_reachable(HUB_DOCS_URL) or _url_reachable(HUB_HEALTH_URL)):
        pytest.skip(
            "hub-api not reachable — start compose (`make compose-up`) for optional e2e smoke"
        )
    return HUB_BASE_URL


@pytest.fixture(scope="session")
def admin_token(hub_base_url: str) -> str:
    del hub_base_url
    token = _resolve_admin_token()
    if not token:
        pytest.skip("ADMIN_BOOTSTRAP_TOKEN not set — export it or add to `.env` for e2e smoke")
    return token


@pytest.fixture(scope="session")
def admin_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session")
def http_client() -> httpx.Client:
    with httpx.Client(timeout=10.0) as client:
        yield client


@pytest.fixture(scope="session")
def partner_public_ids(
    hub_base_url: str,
    admin_headers: dict[str, str],
    http_client: httpx.Client,
) -> dict[str, str]:
    response = http_client.get(
        f"{hub_base_url}/admin/v1/partners",
        headers=admin_headers,
        params={"limit": 100, "offset": 0},
    )
    if response.status_code == 401:
        pytest.skip("admin auth rejected — check ADMIN_BOOTSTRAP_TOKEN matches compose")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    by_slug = {item["slug"]: item["id"] for item in items}
    required = ("acme-erp", "strict-payments")
    missing = [slug for slug in required if slug not in by_slug]
    if missing:
        pytest.skip(
            f"seed partners missing ({', '.join(missing)}) — run `make seed` against compose DB"
        )
    return by_slug

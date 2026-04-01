"""Preflight checks before load scenarios run."""

from __future__ import annotations

import sys

import httpx

from loadtests.config import (
    admin_token,
    load_host,
    partner_public_id_from_env,
    partner_slug,
)

READY_PATH = "/inbound/v1/health"
PARTNERS_PATH = "/admin/v1/partners"
MIN_SMOKE_REQUESTS = 1


class PreflightError(RuntimeError):
    """Raised when load preflight checks fail."""


def preflight_credentials() -> str:
    token = admin_token()
    if not token:
        raise PreflightError(
            "ADMIN_BOOTSTRAP_TOKEN or LOAD_ADMIN_TOKEN must be set in process environment"
        )
    return token


def preflight_api_ready(
    *,
    host: str | None = None,
    timeout_seconds: float = 5.0,
    client: httpx.Client | None = None,
) -> None:
    base = (host or load_host()).rstrip("/")
    try:
        if client is not None:
            response = client.get(READY_PATH, timeout=timeout_seconds)
        else:
            with httpx.Client(base_url=base, timeout=timeout_seconds) as owned:
                response = owned.get(READY_PATH)
        if response.status_code != 200:
            raise PreflightError(f"health check failed: HTTP {response.status_code}")
    except httpx.HTTPError as exc:
        raise PreflightError(f"health check failed: {exc}") from exc


def resolve_partner_public_id(
    *,
    host: str | None = None,
    token: str,
    client: httpx.Client | None = None,
    timeout_seconds: float = 5.0,
) -> str:
    env_id = partner_public_id_from_env()
    if env_id is not None:
        return env_id

    slug = partner_slug()
    base = (host or load_host()).rstrip("/")
    try:
        if client is not None:
            response = client.get(
                PARTNERS_PATH,
                params={"limit": 50, "offset": 0},
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout_seconds,
            )
        else:
            with httpx.Client(base_url=base, timeout=timeout_seconds) as owned:
                response = owned.get(
                    PARTNERS_PATH,
                    params={"limit": 50, "offset": 0},
                    headers={"Authorization": f"Bearer {token}"},
                )
        if response.status_code != 200:
            raise PreflightError(
                f"partner lookup failed for slug {slug}: HTTP {response.status_code}"
            )

        items = response.json().get("items", [])
        for item in items:
            if item.get("slug") == slug:
                return str(item["id"])
        raise PreflightError(f"partner with slug {slug} not found in partners list")
    except httpx.HTTPError as exc:
        raise PreflightError(f"partner lookup failed for slug {slug}: {exc}") from exc


def run_smoke_preflight(
    *,
    host: str | None = None,
    client: httpx.Client | None = None,
) -> str:
    token = preflight_credentials()
    preflight_api_ready(host=host, client=client)
    return resolve_partner_public_id(host=host, token=token, client=client)


def assert_minimum_requests(*, request_count: int, minimum: int = MIN_SMOKE_REQUESTS) -> None:
    if request_count < minimum:
        raise PreflightError(f"{request_count} HTTP request(s) below minimum {minimum}")


def main() -> int:
    try:
        partner_id = run_smoke_preflight()
        print(f"preflight ok partner_public_id={partner_id}")
        return 0
    except PreflightError as exc:
        print(f"preflight failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

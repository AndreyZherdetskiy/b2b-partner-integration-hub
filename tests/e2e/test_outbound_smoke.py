"""Live outbound smoke against Compose hub-api (skipped when stack is down)."""

from __future__ import annotations

import time
import uuid

import httpx
import pytest
from tests.e2e.conftest import POLL_INTERVAL_SECONDS, POLL_TIMEOUT_SECONDS

from app.domain.ids import generate_uuidv7

pytestmark = pytest.mark.e2e


def _post_outbound_event(
    http_client: httpx.Client,
    *,
    base_url: str,
    headers: dict[str, str],
    partner_public_id: str,
    idempotency_key: str,
    correlation_id: uuid.UUID,
) -> httpx.Response:
    request_headers = {
        **headers,
        "X-Correlation-Id": str(correlation_id),
    }
    return http_client.post(
        f"{base_url}/internal/v1/outbound/events",
        headers=request_headers,
        json={
            "partner_id": partner_public_id,
            "event_type": "order.created",
            "payload": {"order_id": f"e2e-{correlation_id}", "source": "e2e-smoke"},
            "idempotency_key": idempotency_key,
            "correlation_id": str(correlation_id),
        },
    )


def _poll_delivery_status(
    http_client: httpx.Client,
    *,
    base_url: str,
    headers: dict[str, str],
    delivery_id: str,
    expected_status: str,
    timeout_seconds: float = POLL_TIMEOUT_SECONDS,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last_status: str | None = None
    while time.monotonic() < deadline:
        response = http_client.get(
            f"{base_url}/admin/v1/deliveries/{delivery_id}",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        last_status = str(body["status"])
        if last_status == expected_status:
            return body
        time.sleep(POLL_INTERVAL_SECONDS)
    msg = (
        f"delivery {delivery_id} did not reach {expected_status!r} "
        f"within {timeout_seconds}s (last={last_status!r})"
    )
    pytest.fail(msg)


def _dead_letter_for_delivery(
    http_client: httpx.Client,
    *,
    base_url: str,
    headers: dict[str, str],
    delivery_id: str,
) -> dict[str, object] | None:
    response = http_client.get(
        f"{base_url}/admin/v1/dead-letters",
        headers=headers,
        params={"limit": 100, "offset": 0},
    )
    assert response.status_code == 200, response.text
    for item in response.json()["items"]:
        if item["delivery_id"] == delivery_id:
            return item
    return None


def _poll_dead_letter(
    http_client: httpx.Client,
    *,
    base_url: str,
    headers: dict[str, str],
    delivery_id: str,
    timeout_seconds: float = POLL_TIMEOUT_SECONDS,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        dead_letter = _dead_letter_for_delivery(
            http_client,
            base_url=base_url,
            headers=headers,
            delivery_id=delivery_id,
        )
        if dead_letter is not None:
            return dead_letter
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f"delivery {delivery_id} not found on dead-letters within {timeout_seconds}s")


def test_admin_partners_list(
    hub_base_url: str,
    admin_headers: dict[str, str],
    http_client: httpx.Client,
    partner_public_ids: dict[str, str],
) -> None:
    assert "acme-erp" in partner_public_ids
    assert "strict-payments" in partner_public_ids
    response = http_client.get(
        f"{hub_base_url}/admin/v1/partners",
        headers=admin_headers,
        params={"limit": 10, "offset": 0},
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] >= 2


def test_acme_erp_outbound_delivered(
    hub_base_url: str,
    admin_headers: dict[str, str],
    http_client: httpx.Client,
    partner_public_ids: dict[str, str],
) -> None:
    correlation_id = generate_uuidv7()
    idempotency_key = f"e2e-acme-{correlation_id}"
    partner_public_id = partner_public_ids["acme-erp"]

    response = _post_outbound_event(
        http_client,
        base_url=hub_base_url,
        headers=admin_headers,
        partner_public_id=partner_public_id,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "accepted"
    delivery_id = str(body["delivery_id"])
    assert delivery_id in {str(item) for item in body["delivery_ids"]}

    delivery = _poll_delivery_status(
        http_client,
        base_url=hub_base_url,
        headers=admin_headers,
        delivery_id=delivery_id,
        expected_status="delivered",
    )
    assert delivery["correlation_id"] == str(correlation_id)
    assert delivery["partner_id"] == partner_public_id


def test_strict_payments_outbound_failed_on_dead_letters(
    hub_base_url: str,
    admin_headers: dict[str, str],
    http_client: httpx.Client,
    partner_public_ids: dict[str, str],
) -> None:
    correlation_id = generate_uuidv7()
    idempotency_key = f"e2e-strict-{correlation_id}"
    partner_public_id = partner_public_ids["strict-payments"]

    response = _post_outbound_event(
        http_client,
        base_url=hub_base_url,
        headers=admin_headers,
        partner_public_id=partner_public_id,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )
    assert response.status_code == 202, response.text
    delivery_id = str(response.json()["delivery_id"])

    delivery = _poll_delivery_status(
        http_client,
        base_url=hub_base_url,
        headers=admin_headers,
        delivery_id=delivery_id,
        expected_status="failed",
    )
    assert delivery["status"] == "failed"

    dead_letter = _poll_dead_letter(
        http_client,
        base_url=hub_base_url,
        headers=admin_headers,
        delivery_id=delivery_id,
    )
    assert dead_letter["delivery_id"] == delivery_id
    assert dead_letter["partner_id"] == partner_public_id
    assert dead_letter["reason"] == "non_retryable_error"
    assert dead_letter["last_http_status"] == 400

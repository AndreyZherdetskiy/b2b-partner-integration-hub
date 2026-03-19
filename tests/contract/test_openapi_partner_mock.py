"""Contract tests: partner-mock webhook headers and hub OpenAPI inbound path."""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient
from partner_mock.app import create_app as create_partner_mock_app

from app.domain.services.hmac_service import sign
from app.main import create_app

SIGNING_SECRET = b"contract-test-signing-secret"


def _signed_webhook_headers(body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    return {
        "Content-Type": "application/json",
        "X-Hub-Timestamp": timestamp,
        "X-Hub-Signature-256": sign(SIGNING_SECRET, timestamp, body),
        "Idempotency-Key": "contract-idem-1",
        "X-Mock-Scenario": "ok",
    }


def test_partner_mock_webhook_accepts_signature_and_idempotency_key() -> None:
    client = TestClient(create_partner_mock_app())
    body = json.dumps({"event_type": "order.created", "payload": {"order_id": "1"}}).encode()
    response = client.post("/webhook", content=body, headers=_signed_webhook_headers(body))
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_openapi_inbound_events_documents_signature_and_idempotency_headers() -> None:
    spec = create_app().openapi()
    post = spec["paths"]["/inbound/v1/{partner_slug}/events"]["post"]
    parameters = post.get("parameters", [])
    header_names = {p["name"] for p in parameters if p.get("in") == "header"}
    assert "X-Hub-Signature-256" in header_names
    assert "Idempotency-Key" in header_names

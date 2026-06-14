"""Admin partners/endpoints/API keys integration tests (PostgreSQL)."""

from __future__ import annotations

import uuid

import jwt
import pytest
from fastapi.testclient import TestClient
from tests.integration.conftest import INTEGRATION_ADMIN_TOKEN, auth_header

pytestmark = pytest.mark.integration


def _viewer_token(secret: str = INTEGRATION_ADMIN_TOKEN) -> str:
    return jwt.encode({"sub": "viewer-1", "role": "hub_viewer"}, secret, algorithm="HS256")


def test_create_partner_returns_uuid_and_signing_secret_once(client: TestClient) -> None:
    res = client.post(
        "/admin/v1/partners",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
        json={"slug": "acme-erp", "name": "ACME ERP", "sla_seconds": 60},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert uuid.UUID(body["id"]).version == 7
    assert body["slug"] == "acme-erp"
    assert body["signing_secret"]
    assert isinstance(body["signing_secret"], str)


def test_list_and_get_partner(client: TestClient) -> None:
    created = client.post(
        "/admin/v1/partners",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
        json={"slug": "globex", "name": "Globex", "sla_seconds": 120},
    ).json()
    listed = client.get(
        "/admin/v1/partners",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
        params={"limit": 10, "offset": 0},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1
    assert any(p["id"] == created["id"] for p in listed.json()["items"])

    detail = client.get(
        f"/admin/v1/partners/{created['id']}",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
    )
    assert detail.status_code == 200
    assert "signing_secret" not in detail.json()


def test_patch_partner(client: TestClient) -> None:
    created = client.post(
        "/admin/v1/partners",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
        json={"slug": "initech", "name": "Initech", "sla_seconds": 60},
    ).json()
    patched = client.patch(
        f"/admin/v1/partners/{created['id']}",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
        json={"sla_seconds": 90, "status": "active"},
    )
    assert patched.status_code == 200
    assert patched.json()["sla_seconds"] == 90
    assert patched.json()["status"] == "active"


def test_create_api_key_returns_plaintext_once(client: TestClient) -> None:
    partner = client.post(
        "/admin/v1/partners",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
        json={"slug": "umbrella", "name": "Umbrella", "sla_seconds": 60},
    ).json()
    res = client.post(
        f"/admin/v1/partners/{partner['id']}/api-keys",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
        json={"scopes": ["inbound:write"]},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["key"].startswith("pk_live_")
    assert body["key_prefix"]
    assert uuid.UUID(body["id"]).version == 7


def test_create_and_get_endpoint(client: TestClient) -> None:
    partner = client.post(
        "/admin/v1/partners",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
        json={"slug": "wayne", "name": "Wayne", "sla_seconds": 60},
    ).json()
    created = client.post(
        f"/admin/v1/partners/{partner['id']}/endpoints",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
        json={
            "direction": "outbound",
            "url": "https://partner.example/hooks",
            "event_types": ["order.created"],
        },
    )
    assert created.status_code == 201, created.text
    ep = created.json()
    assert ep["partner_id"] == partner["id"]
    assert uuid.UUID(ep["id"]).version == 7

    fetched = client.get(
        f"/admin/v1/endpoints/{ep['id']}",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
    )
    assert fetched.status_code == 200


def test_patch_endpoint_pause(client: TestClient) -> None:
    partner = client.post(
        "/admin/v1/partners",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
        json={"slug": "stark", "name": "Stark", "sla_seconds": 60},
    ).json()
    ep = client.post(
        f"/admin/v1/partners/{partner['id']}/endpoints",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
        json={
            "direction": "outbound",
            "url": "https://partner.example/hooks",
            "event_types": ["order.updated"],
        },
    ).json()
    patched = client.patch(
        f"/admin/v1/endpoints/{ep['id']}",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
        json={"status": "paused"},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "paused"


def test_viewer_can_get_cannot_post(client: TestClient) -> None:
    token = _viewer_token()
    get_res = client.get("/admin/v1/partners", headers=auth_header(token))
    assert get_res.status_code == 200
    post_res = client.post(
        "/admin/v1/partners",
        headers=auth_header(token),
        json={"slug": "denied", "name": "Denied", "sla_seconds": 60},
    )
    assert post_res.status_code == 403


def test_missing_auth_returns_401(client: TestClient) -> None:
    res = client.get("/admin/v1/partners")
    assert res.status_code == 401


def test_invalid_token_returns_401(client: TestClient) -> None:
    res = client.get("/admin/v1/partners", headers=auth_header("not-a-valid-token"))
    assert res.status_code == 401


def test_partner_not_found_returns_404(client: TestClient) -> None:
    missing = "018e1234-5678-7abc-8def-123456789abc"
    res = client.get(
        f"/admin/v1/partners/{missing}",
        headers=auth_header(INTEGRATION_ADMIN_TOKEN),
    )
    assert res.status_code == 404

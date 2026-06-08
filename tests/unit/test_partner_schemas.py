"""Partner / endpoint admin schema contract tests (OpenAPI + dual-id)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain.enums import EndpointDirection, EndpointStatus, PartnerStatus
from app.main import create_app
from app.schemas.endpoint import EndpointResponse
from app.schemas.partner import (
    ApiKeyCreatedResponse,
    PartnerCreate,
    PartnerCreatedResponse,
    PartnerResponse,
)

SKIP_SCHEMA = {"ValidationError", "HTTPValidationError"}


def test_partner_response_id_is_uuid_not_integer() -> None:
    pid = uuid.UUID("018e1234-5678-7abc-8def-123456789abc")
    resp = PartnerResponse(
        id=pid,
        slug="acme-erp",
        name="ACME ERP",
        status=PartnerStatus.PROVISIONING,
        sla_seconds=60,
        rate_limit_rps=100,
        auto_replay_enabled=False,
        circuit_breaker_config={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert resp.id == pid
    schema = PartnerResponse.model_json_schema()
    id_prop = schema["properties"]["id"]
    assert id_prop.get("format") == "uuid"


def test_partner_created_includes_signing_secret_once() -> None:
    pid = uuid.UUID("018e1234-5678-7abc-8def-123456789abc")
    created = PartnerCreatedResponse(
        id=pid,
        slug="acme-erp",
        name="ACME ERP",
        status=PartnerStatus.PROVISIONING,
        sla_seconds=60,
        rate_limit_rps=100,
        auto_replay_enabled=False,
        circuit_breaker_config={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        signing_secret="whsec_test_plaintext_once",
    )
    assert created.signing_secret == "whsec_test_plaintext_once"
    props = PartnerCreatedResponse.model_json_schema()["properties"]
    assert "signing_secret" in props
    assert "description" in props["signing_secret"]


def test_partner_create_requires_slug_name_sla() -> None:
    with pytest.raises(ValidationError):
        PartnerCreate.model_validate({"slug": "x", "name": "X"})


def test_endpoint_response_partner_id_is_uuid() -> None:
    eid = uuid.UUID("018e1234-5678-7abc-8def-123456789abd")
    pid = uuid.UUID("018e1234-5678-7abc-8def-123456789abc")
    resp = EndpointResponse(
        id=eid,
        partner_id=pid,
        direction=EndpointDirection.OUTBOUND,
        url="https://partner.example/hooks",
        event_types=["order.created"],
        status=EndpointStatus.ACTIVE,
        sla_seconds=None,
        max_attempts=8,
        timeout_connect_ms=3000,
        timeout_read_ms=10000,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert resp.partner_id == pid
    fmt = EndpointResponse.model_json_schema()["properties"]["partner_id"].get("format")
    assert fmt == "uuid"


def test_api_key_created_returns_plaintext_and_prefix() -> None:
    kid = uuid.UUID("018e1234-5678-7abc-8def-123456789abe")
    resp = ApiKeyCreatedResponse(
        id=kid,
        key="pk_live_abcdefghijklmnop",
        key_prefix="pk_live_abcd",
        scopes=["inbound:write"],
        expires_at=None,
        created_at=datetime.now(UTC),
    )
    assert resp.key.startswith("pk_live_")
    assert resp.key_prefix == "pk_live_abcd"


def test_all_partner_endpoint_schema_properties_have_descriptions() -> None:
    spec = create_app().openapi()
    target_prefixes = (
        "Partner",
        "Endpoint",
        "ApiKey",
        "Paginated",
        "AdminError",
    )
    for name, schema in spec.get("components", {}).get("schemas", {}).items():
        if name in SKIP_SCHEMA:
            continue
        if not any(name.startswith(p) or p in name for p in target_prefixes):
            continue
        for prop_name, prop in schema.get("properties", {}).items():
            assert "description" in prop, f"{name}.{prop_name} missing description"


def test_partner_create_openapi_has_example() -> None:
    spec = create_app().openapi()
    path = spec["paths"]["/admin/v1/partners"]["post"]
    body = path["requestBody"]["content"]["application/json"]
    assert "examples" in body or "example" in body


def test_enum_fields_not_bare_string_in_openapi() -> None:
    spec = create_app().openapi()
    partner_resp = spec["components"]["schemas"]["PartnerResponse"]
    status = partner_resp["properties"]["status"]
    assert "$ref" in status or "enum" in status or "allOf" in status

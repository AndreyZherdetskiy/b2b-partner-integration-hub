from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from app.main import create_app

SKIP_SCHEMA = {"ValidationError", "HTTPValidationError"}
_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
_FORBIDDEN_PROSE = re.compile(r"spec\.md|ADR-\d|\.superpowers|\.docs_ru|AGENTS\.md", re.I)
_INBOUND_HMAC_HEADERS = (
    "X-Hub-Signature-256",
    "X-Hub-Timestamp",
    "Idempotency-Key",
    "authorization",
)
_BODYLESS_POSTS = {
    ("post", "/admin/v1/partners/{id}/rotate-secret"),
    ("post", "/admin/v1/replay-approvals/{id}/approve"),
    ("post", "/admin/v1/dead-letters/{id}/ack"),
}


def _operations(spec: dict) -> list[tuple[str, str, dict]]:
    ops: list[tuple[str, str, dict]] = []
    for path, item in spec.get("paths", {}).items():
        for method, operation in item.items():
            if method not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            ops.append((method, path, operation))
    return ops


def test_openapi_info_and_tags() -> None:
    spec = create_app().openapi()
    assert spec["info"]["title"] == "Partner Integration Hub"
    assert spec["info"].get("summary")
    assert spec["info"].get("description")
    names = {t["name"] for t in spec["tags"]}
    assert {"inbound", "admin", "internal", "partner", "health"} <= names
    for tag in spec["tags"]:
        assert tag.get("description")
    servers = {s.get("url") for s in spec.get("servers", [])}
    assert "http://localhost:8000" in servers


def test_schema_properties_have_descriptions() -> None:
    spec = create_app().openapi()
    for name, schema in spec.get("components", {}).get("schemas", {}).items():
        if name in SKIP_SCHEMA:
            continue
        for prop_name, prop in schema.get("properties", {}).items():
            assert "description" in prop, f"{name}.{prop_name} missing description"


def test_operations_have_summary_and_description() -> None:
    spec = create_app().openapi()
    missing_summary: list[str] = []
    missing_description: list[str] = []
    for method, path, operation in _operations(spec):
        label = f"{method.upper()} {path}"
        if not operation.get("summary"):
            missing_summary.append(label)
        if not operation.get("description"):
            missing_description.append(label)
    assert missing_summary == []
    assert missing_description == []


def test_json_bodies_have_request_examples() -> None:
    spec = create_app().openapi()
    missing: list[str] = []
    for method, path, operation in _operations(spec):
        if (method, path) in _BODYLESS_POSTS:
            continue
        request_body = operation.get("requestBody")
        if not request_body:
            continue
        json_content = request_body.get("content", {}).get("application/json", {})
        if not json_content:
            continue
        if not (json_content.get("examples") or json_content.get("example")):
            missing.append(f"{method.upper()} {path}")
    assert missing == []


def test_inbound_hmac_headers_documented() -> None:
    spec = create_app().openapi()
    post = spec["paths"]["/inbound/v1/{partner_slug}/events"]["post"]
    headers = {
        param["name"]: param for param in post.get("parameters", []) if param.get("in") == "header"
    }
    assert "Content-Type" not in headers
    for name in _INBOUND_HMAC_HEADERS:
        assert name in headers, f"missing header {name}"
        assert headers[name].get("description"), f"{name} missing description"
    assert "409" in post["responses"]
    assert "413" in post["responses"]
    description = post.get("description") or ""
    assert "HMAC-SHA256" in description
    assert "202" in description
    assert "200" in description


def test_internal_outbound_documents_rbac_and_conflict() -> None:
    spec = create_app().openapi()
    post = spec["paths"]["/internal/v1/outbound/events"]["post"]
    for code in ("403", "409"):
        assert code in post["responses"]
    assert post.get("description")


def test_openapi_prose_has_no_internal_doc_paths() -> None:
    blob = json.dumps(create_app().openapi())
    match = _FORBIDDEN_PROSE.search(blob)
    assert match is None, f"OpenAPI cites internal path {match.group(0)}"


def test_docs_html() -> None:
    client = TestClient(create_app())
    res = client.get("/docs")
    assert res.status_code == 200
    assert "swagger" in res.text.lower()


def test_no_sequential_id_on_partner_delivery_schemas() -> None:
    spec = create_app().openapi()
    schemas = spec.get("components", {}).get("schemas", {})
    for key, schema in schemas.items():
        if "Partner" in key or "Delivery" in key:
            props = schema.get("properties", {})
            if "id" in props:
                t = props["id"].get("type")
                fmt = props["id"].get("format")
                assert t != "integer", f"{key}.id must not be integer BIGINT"
                if t == "string":
                    assert fmt in {"uuid", None} or "uuid" in str(props["id"]).lower()

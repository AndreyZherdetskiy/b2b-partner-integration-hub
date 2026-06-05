"""Unit tests for weekly SLA compliance export (Stage 3 Task 5)."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.config import get_settings
from app.domain.enums import DeliveryStatus
from app.main import create_app

ADMIN_TOKEN = "test-admin-bootstrap-token-at-least-32-bytes"
CSV_HEADER = "partner_slug,success_rate,sla_compliance_pct,sla_breaches,dlq_count"
WINDOW_FROM = "2026-06-01T00:00:00+00:00"
WINDOW_TO = "2026-06-08T00:00:00+00:00"

COMPLIANCE_EXPORT_PATH = "/admin/v1/analytics/compliance-export"


class FakeResult:
    def __init__(self, *, rows: list[object] | None = None) -> None:
        self._rows = rows if rows is not None else []

    def all(self) -> list[object]:
        return self._rows


class ComplianceExportSession:
    def __init__(
        self,
        *,
        delivery_rows: list[tuple[str, str, bool]],
        dlq_rows: list[tuple[str, int]],
    ) -> None:
        self._delivery_rows = delivery_rows
        self._dlq_rows = dlq_rows
        self._call = 0

    async def execute(self, _stmt: object) -> FakeResult:
        self._call += 1
        if self._call == 1:
            return FakeResult(rows=self._delivery_rows)
        if self._call == 2:
            return FakeResult(rows=self._dlq_rows)
        raise AssertionError(f"unexpected execute call {self._call}")


class EmptyComplianceSession:
    def __init__(self) -> None:
        self._call = 0

    async def execute(self, _stmt: object) -> FakeResult:
        self._call += 1
        return FakeResult(rows=[])


@contextmanager
def _build_app(session: object) -> Iterator[TestClient]:
    os.environ["ADMIN_BOOTSTRAP_TOKEN"] = ADMIN_TOKEN
    get_settings.cache_clear()
    app = create_app()

    async def override_db() -> AsyncIterator[object]:
        yield session

    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()
    app.dependency_overrides.clear()


def _role_token(role: str, secret: str = ADMIN_TOKEN) -> str:
    return jwt.encode({"sub": f"user-{role}", "role": role}, secret, algorithm="HS256")


def _auth(token: str | None = ADMIN_TOKEN) -> dict[str, str]:
    if token is None:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _export_params(
    *,
    from_dt: str | None = WINDOW_FROM,
    to_dt: str | None = WINDOW_TO,
) -> dict[str, str]:
    params: dict[str, str] = {}
    if from_dt is not None:
        params["from"] = from_dt
    if to_dt is not None:
        params["to"] = to_dt
    return params


@pytest.fixture(autouse=True)
def _noop_kafka_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(self: object) -> None:
        return None

    monkeypatch.setattr("aiokafka.AIOKafkaProducer.start", _noop)
    monkeypatch.setattr("aiokafka.AIOKafkaProducer.stop", _noop)


def test_viewer_gets_csv_with_header() -> None:
    session = ComplianceExportSession(
        delivery_rows=[
            ("acme", DeliveryStatus.DELIVERED.value, False),
            ("acme", DeliveryStatus.FAILED.value, True),
        ],
        dlq_rows=[("acme", 2)],
    )
    with _build_app(session) as client:
        res = client.get(
            COMPLIANCE_EXPORT_PATH,
            params=_export_params(),
            headers={**_auth(_role_token("hub_viewer")), "Accept": "text/csv"},
        )
        assert res.status_code == 200, res.text
        assert res.headers["content-type"].startswith("text/csv")
        lines = res.text.strip().splitlines()
        assert lines[0] == CSV_HEADER
        assert len(lines) == 2
        assert lines[1].startswith("acme,")


def test_empty_window_returns_header_only() -> None:
    with _build_app(EmptyComplianceSession()) as client:
        res = client.get(
            COMPLIANCE_EXPORT_PATH,
            params=_export_params(),
            headers={**_auth(), "Accept": "text/csv"},
        )
        assert res.status_code == 200, res.text
        assert res.text.strip() == CSV_HEADER


def test_missing_from_or_to_returns_422() -> None:
    with _build_app(EmptyComplianceSession()) as client:
        missing_from = client.get(
            COMPLIANCE_EXPORT_PATH,
            params=_export_params(from_dt=None),
            headers=_auth(),
        )
        missing_to = client.get(
            COMPLIANCE_EXPORT_PATH,
            params=_export_params(to_dt=None),
            headers=_auth(),
        )
        assert missing_from.status_code == 422
        assert missing_to.status_code == 422


def test_to_not_after_from_returns_422() -> None:
    with _build_app(EmptyComplianceSession()) as client:
        res = client.get(
            COMPLIANCE_EXPORT_PATH,
            params=_export_params(from_dt=WINDOW_TO, to_dt=WINDOW_FROM),
            headers=_auth(),
        )
        assert res.status_code == 422


def test_missing_auth_returns_401() -> None:
    with _build_app(EmptyComplianceSession()) as client:
        res = client.get(
            COMPLIANCE_EXPORT_PATH,
            params=_export_params(),
            headers=_auth(None),
        )
        assert res.status_code == 401


def test_json_accept_returns_rows_array() -> None:
    session = ComplianceExportSession(
        delivery_rows=[
            ("beta", DeliveryStatus.DELIVERED.value, False),
            ("beta", DeliveryStatus.DELIVERED.value, False),
        ],
        dlq_rows=[("beta", 1)],
    )
    with _build_app(session) as client:
        res = client.get(
            COMPLIANCE_EXPORT_PATH,
            params=_export_params(),
            headers={**_auth(), "Accept": "application/json"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert "rows" in body
        assert len(body["rows"]) == 1
        row = body["rows"][0]
        assert row["partner_slug"] == "beta"
        assert row["success_rate"] == pytest.approx(1.0)
        assert row["sla_compliance_pct"] == pytest.approx(100.0)
        assert row["sla_breaches"] == 0
        assert row["dlq_count"] == 1


def test_response_body_has_no_integer_partner_or_delivery_ids() -> None:
    session = ComplianceExportSession(
        delivery_rows=[("gamma", DeliveryStatus.DELIVERED.value, False)],
        dlq_rows=[],
    )
    with _build_app(session) as client:
        res = client.get(
            COMPLIANCE_EXPORT_PATH,
            params=_export_params(),
            headers={**_auth(), "Accept": "application/json"},
        )
        assert res.status_code == 200, res.text
        serialized = res.text
        assert not re.search(r'"partner_id"\s*:\s*\d+', serialized)
        assert not re.search(r'"delivery_id"\s*:\s*\d+', serialized)
        for row in res.json()["rows"]:
            assert "partner_slug" in row
            for value in row.values():
                if isinstance(value, str) and value.isdigit():
                    pytest.fail(f"unexpected numeric string id in row: {row}")


def test_openapi_compliance_export_schema() -> None:
    spec = create_app().openapi()
    path = "/admin/v1/analytics/compliance-export"
    assert path in spec["paths"]
    route = spec["paths"][path]["get"]
    assert "admin" in route["tags"]
    for code in ("401", "403", "422"):
        assert code in route["responses"]
    schemas = spec.get("components", {}).get("schemas", {})
    assert "ComplianceExportResponse" in schemas
    row_schema = schemas["ComplianceExportRow"]
    props = row_schema.get("properties", {})
    assert "partner_slug" in props
    assert props["partner_slug"].get("description")
    assert props["success_rate"].get("description")
    assert props["sla_compliance_pct"].get("description")
    assert props["sla_breaches"].get("description")
    assert props["dlq_count"].get("description")
    if "partner_id" in props:
        assert props["partner_id"].get("type") != "integer"


def test_jwt_secret_at_least_32_bytes() -> None:
    assert len(ADMIN_TOKEN.encode("utf-8")) >= 32

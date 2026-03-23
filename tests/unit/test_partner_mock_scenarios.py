"""Partner mock chaos scenarios via X-Mock-Scenario header (no Docker)."""

import pytest
from fastapi.testclient import TestClient
from partner_mock.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_unknown_scenario_defaults_to_200(client: TestClient) -> None:
    response = client.post("/webhook", headers={"X-Mock-Scenario": "unknown"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_missing_scenario_defaults_to_200(client: TestClient) -> None:
    response = client.post("/webhook")
    assert response.status_code == 200


def test_ok_scenario_returns_200(client: TestClient) -> None:
    response = client.post("/webhook", headers={"X-Mock-Scenario": "ok"})
    assert response.status_code == 200
    assert response.json()["scenario"] == "ok"


def test_fail_400_returns_400(client: TestClient) -> None:
    response = client.post("/webhook", headers={"X-Mock-Scenario": "fail_400"})
    assert response.status_code == 400


def test_fail_503_returns_503(client: TestClient) -> None:
    response = client.post("/webhook", headers={"X-Mock-Scenario": "fail_503"})
    assert response.status_code == 503


def test_fail_429_returns_429(client: TestClient) -> None:
    response = client.post("/webhook", headers={"X-Mock-Scenario": "fail_429"})
    assert response.status_code == 429


def test_timeout_scenario_delays_before_response(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("partner_mock.app.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("partner_mock.app.MOCK_TIMEOUT_SECONDS", 30.0)

    response = client.post("/webhook", headers={"X-Mock-Scenario": "timeout"})
    assert response.status_code == 200
    assert response.json()["scenario"] == "timeout"
    assert delays == [30.0]


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_fail_503_then_ok_returns_503_three_times_then_200(client: TestClient) -> None:
    headers = {
        "X-Mock-Scenario": "fail_503_then_ok",
        "Idempotency-Key": "idem-flaky-1",
    }
    for _ in range(3):
        response = client.post("/webhook", headers=headers)
        assert response.status_code == 503
        assert response.json()["scenario"] == "fail_503_then_ok"

    response = client.post("/webhook", headers=headers)
    assert response.status_code == 200
    assert response.json()["scenario"] == "fail_503_then_ok"

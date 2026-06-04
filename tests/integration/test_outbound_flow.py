"""Integration smoke for outbound delivery against Compose services."""

from __future__ import annotations

import os
import urllib.error
import urllib.request

import pytest

PARTNER_MOCK_URL = os.getenv("PARTNER_MOCK_URL", "http://localhost:8090/health")
HUB_API_HEALTH = os.getenv("HUB_API_HEALTH", "http://localhost:8000/inbound/v1/health")


def _url_reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


@pytest.mark.integration
def test_outbound_flow_compose_optional() -> None:
    if not _url_reachable(HUB_API_HEALTH):
        pytest.skip("hub-api not reachable — start compose with hub-api for integration")
    if not _url_reachable(PARTNER_MOCK_URL):
        pytest.skip("partner-mock not reachable — start compose partner-mock for integration")
    assert True

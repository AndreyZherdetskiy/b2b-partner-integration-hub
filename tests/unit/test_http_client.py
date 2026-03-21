"""Unit tests for outbound httpx client (spec §4.7 / §7.5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.integrations.http_client import post_outbound


@pytest.mark.asyncio
async def test_post_outbound_passes_per_call_timeout_to_injected_client() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.text = "ok"

    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=mock_response)

    await post_outbound(
        url="https://partner.example/webhook",
        body_bytes=b'{"event":"test"}',
        headers={"Content-Type": "application/json"},
        timeout_connect_s=1.5,
        timeout_read_s=12.0,
        client=client,
    )

    client.post.assert_awaited_once()
    _, kwargs = client.post.call_args
    timeout = kwargs["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 1.5
    assert timeout.read == 12.0

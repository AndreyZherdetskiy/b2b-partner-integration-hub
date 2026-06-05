"""httpx client for outbound partner webhook POSTs (spec §7.5)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.domain.services.hmac_service import sign


@dataclass(frozen=True)
class OutboundPostResult:
    http_status_code: int | None
    response_headers: dict[str, str]
    response_body: str
    duration_ms: int
    error_type: str | None


def serialize_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_outbound_headers(
    *,
    delivery_public_id: str,
    event_type: str,
    timestamp: str,
    body_bytes: bytes,
    signing_secret: bytes,
    idempotency_key: str,
    correlation_id: str,
) -> dict[str, str]:
    signature = sign(signing_secret, timestamp, body_bytes)
    return {
        "Content-Type": "application/json",
        "X-Hub-Delivery-Id": delivery_public_id,
        "X-Hub-Event-Type": event_type,
        "X-Hub-Timestamp": timestamp,
        "X-Hub-Signature-256": signature,
        "Idempotency-Key": idempotency_key,
        "X-Correlation-Id": correlation_id,
    }


async def post_outbound(
    *,
    url: str,
    body_bytes: bytes,
    headers: dict[str, str],
    timeout_connect_s: float,
    timeout_read_s: float,
    client: httpx.AsyncClient | None = None,
) -> OutboundPostResult:
    timeout = httpx.Timeout(timeout_read_s, connect=timeout_connect_s)
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=timeout)
    requested_at_ms = _now_ms()
    try:
        response = await http_client.post(
            url, content=body_bytes, headers=headers, timeout=timeout
        )
        duration_ms = _now_ms() - requested_at_ms
        return OutboundPostResult(
            http_status_code=response.status_code,
            response_headers=dict(response.headers),
            response_body=response.text,
            duration_ms=duration_ms,
            error_type=None,
        )
    except httpx.TimeoutException:
        duration_ms = _now_ms() - requested_at_ms
        return OutboundPostResult(
            http_status_code=None,
            response_headers={},
            response_body="",
            duration_ms=duration_ms,
            error_type="timeout",
        )
    except httpx.ConnectError:
        duration_ms = _now_ms() - requested_at_ms
        return OutboundPostResult(
            http_status_code=None,
            response_headers={},
            response_body="",
            duration_ms=duration_ms,
            error_type="connect_error",
        )
    except httpx.NetworkError as exc:
        duration_ms = _now_ms() - requested_at_ms
        return OutboundPostResult(
            http_status_code=None,
            response_headers={},
            response_body="",
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
        )
    finally:
        if owns_client:
            await http_client.aclose()


def _now_ms() -> int:
    return int(time.monotonic() * 1000)

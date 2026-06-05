"""HMAC-SHA256 signing and verification for inbound/outbound webhooks (ADR-004)."""

import hashlib
import hmac

_SIGNATURE_PREFIX = "sha256="


def signed_payload(timestamp: str, body: bytes) -> bytes:
    return f"{timestamp}.".encode() + body


def _secret_bytes(secret: bytes | str) -> bytes:
    if isinstance(secret, bytes):
        return secret
    return secret.encode("utf-8")


def _compute_hex(secret: bytes | str, payload: bytes) -> str:
    return hmac.new(_secret_bytes(secret), payload, hashlib.sha256).hexdigest()


def sign(secret: bytes | str, timestamp: str, body: bytes) -> str:
    return f"{_SIGNATURE_PREFIX}{_compute_hex(secret, signed_payload(timestamp, body))}"


def _header_hex(header: str) -> str | None:
    if not header.startswith(_SIGNATURE_PREFIX):
        return None
    return header[len(_SIGNATURE_PREFIX) :]


def _matches_secret(secret: bytes | str, payload: bytes, header_hex: str) -> bool:
    expected = _compute_hex(secret, payload)
    return hmac.compare_digest(expected, header_hex)


def verify(
    secret: bytes | str,
    timestamp: str,
    body: bytes,
    header: str,
    *,
    now: int,
    tolerance: int = 300,
    previous_secret: bytes | str | None = None,
) -> bool:
    ts = int(timestamp)
    if abs(now - ts) > tolerance:
        return False

    header_hex = _header_hex(header)
    if header_hex is None:
        return False

    payload = signed_payload(timestamp, body)
    if _matches_secret(secret, payload, header_hex):
        return True
    return previous_secret is not None and _matches_secret(previous_secret, payload, header_hex)

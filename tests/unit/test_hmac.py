"""Unit tests for HMAC-SHA256 signing and verification (ADR-004, spec §7.1.1)."""

import json

from app.domain.services.hmac_service import sign, signed_payload, verify


def test_signed_payload_format() -> None:
    body = b'{"a":1}'
    assert signed_payload("1720000000", body) == b"1720000000." + body


def test_valid_signature() -> None:
    body = b'{"a":1}'
    ts = "1720000000"
    sig = sign("secret", ts, body)
    assert sig.startswith("sha256=")
    assert verify("secret", ts, body, sig, now=1720000000) is True


def test_tampered_body_rejected() -> None:
    body = b'{"a":1}'
    ts = "1720000000"
    sig = sign("secret", ts, body)
    tampered = b'{"a":2}'
    assert verify("secret", ts, tampered, sig, now=1720000000) is False


def test_skew_rejected() -> None:
    body = b"{}"
    ts = "1"
    sig = sign("secret", ts, body)
    assert verify("secret", ts, body, sig, now=100000, tolerance=300) is False


def test_previous_secret_accepted() -> None:
    body = b"{}"
    ts = "100"
    sig = sign("old", ts, body)
    assert verify("new", ts, body, sig, now=100, previous_secret="old") is True


def test_wrong_secret_without_previous_rejected() -> None:
    body = b"{}"
    ts = "100"
    sig = sign("old", ts, body)
    assert verify("new", ts, body, sig, now=100) is False


def test_raw_body_not_json_reencoded() -> None:
    # Compact JSON bytes differ from json.dumps default (spaces after separators).
    body = b'{"a":1,"b":2}'
    ts = "1720000000"
    sig = sign("secret", ts, body)
    reencoded = json.dumps(json.loads(body)).encode()
    assert reencoded != body
    assert verify("secret", ts, body, sig, now=1720000000) is True
    assert verify("secret", ts, reencoded, sig, now=1720000000) is False

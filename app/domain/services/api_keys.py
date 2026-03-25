"""API key generation and verification helpers."""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher

_KEY_PREFIX = "pk_live_"
_HASHER = PasswordHasher()


def generate_api_key() -> tuple[str, str, str]:
    """Return (full_key, key_prefix, argon2_hash)."""
    suffix = secrets.token_urlsafe(24)
    full_key = f"{_KEY_PREFIX}{suffix}"
    prefix = full_key[:16]
    key_hash = _HASHER.hash(full_key)
    return full_key, prefix, key_hash


def generate_signing_secret() -> str:
    """Return a new plaintext signing secret for HMAC."""
    return f"whsec_{secrets.token_urlsafe(32)}"


def extract_prefix(full_key: str) -> str:
    """Return the lookup prefix (first 16 chars) for an API key."""
    return full_key[:16]


def verify_api_key(full_key: str, key_hash: str) -> bool:
    """Verify a plaintext API key against an argon2 hash."""
    try:
        return _HASHER.verify(key_hash, full_key)
    except Exception:
        return False

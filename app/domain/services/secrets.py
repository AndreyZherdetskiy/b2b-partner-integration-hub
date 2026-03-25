"""Fernet helpers for signing secrets at rest (Stage 1 partners column)."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretDecryptionError(ValueError):
    """Raised when a stored secret cannot be decrypted."""


def encrypt_signing_secret(plaintext: bytes, fernet_key: str) -> bytes:
    """Encrypt a signing secret for BYTEA storage."""
    return Fernet(fernet_key.encode("ascii")).encrypt(plaintext)


def decrypt_signing_secret(encrypted: bytes, fernet_key: str) -> bytes:
    """Decrypt a signing secret from BYTEA storage."""
    try:
        return Fernet(fernet_key.encode("ascii")).decrypt(encrypted)
    except InvalidToken as exc:
        raise SecretDecryptionError("invalid Fernet token") from exc

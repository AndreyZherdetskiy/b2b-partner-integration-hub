"""Outbound HTTP attempt classification and helpers."""

from __future__ import annotations

from enum import StrEnum

import httpx

_DEFAULT_POISON_CODES: frozenset[int] = frozenset({400, 401, 403, 404, 422})
_DEFAULT_RETRYABLE_CODES: frozenset[int] = frozenset({408, 429})

RESPONSE_BODY_MAX_LEN = 4096


class ResponseClassification(StrEnum):
    SUCCESS = "success"
    RETRYABLE = "retryable"
    POISON = "poison"


def truncate_response_body(text: str) -> str:
    if len(text) <= RESPONSE_BODY_MAX_LEN:
        return text
    return text[:RESPONSE_BODY_MAX_LEN]


def classify_http_outcome(
    status_code: int | None,
    *,
    retry_on_status_codes: list[int],
    error: BaseException | None = None,
) -> ResponseClassification:
    if error is not None:
        if isinstance(
            error,
            httpx.TimeoutException | httpx.ConnectError | httpx.NetworkError,
        ):
            return ResponseClassification.RETRYABLE
        return ResponseClassification.POISON

    if status_code is None:
        return ResponseClassification.RETRYABLE

    if 200 <= status_code < 300:
        return ResponseClassification.SUCCESS

    if status_code >= 500:
        return ResponseClassification.RETRYABLE

    custom = set(retry_on_status_codes)
    if custom:
        if status_code in custom:
            return ResponseClassification.RETRYABLE
        if 400 <= status_code < 500:
            return ResponseClassification.POISON
        return ResponseClassification.POISON

    if status_code in _DEFAULT_RETRYABLE_CODES:
        return ResponseClassification.RETRYABLE
    if status_code in _DEFAULT_POISON_CODES:
        return ResponseClassification.POISON
    if 400 <= status_code < 500:
        return ResponseClassification.POISON
    return ResponseClassification.POISON

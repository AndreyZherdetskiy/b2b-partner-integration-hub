"""Unit tests for outbound HTTP response poison vs retry classification."""

import httpx
import pytest

from app.domain.services.delivery_attempt import (
    ResponseClassification,
    classify_http_outcome,
)

DEFAULT_POISON_CODES = [400, 401, 403, 404, 422]
DEFAULT_RETRYABLE_CODES = [408, 429, 500, 502, 503, 504]


@pytest.mark.parametrize("status_code", [200, 201, 204])
def test_2xx_is_success(status_code: int) -> None:
    outcome = classify_http_outcome(status_code, retry_on_status_codes=[])
    assert outcome == ResponseClassification.SUCCESS


@pytest.mark.parametrize("status_code", DEFAULT_RETRYABLE_CODES)
def test_default_retryable_status_codes(status_code: int) -> None:
    outcome = classify_http_outcome(status_code, retry_on_status_codes=[])
    assert outcome == ResponseClassification.RETRYABLE


@pytest.mark.parametrize("status_code", DEFAULT_POISON_CODES)
def test_default_poison_status_codes(status_code: int) -> None:
    outcome = classify_http_outcome(status_code, retry_on_status_codes=[])
    assert outcome == ResponseClassification.POISON


def test_default_other_4xx_is_poison() -> None:
    assert classify_http_outcome(405, retry_on_status_codes=[]) == ResponseClassification.POISON


def test_network_timeout_is_retryable() -> None:
    assert (
        classify_http_outcome(
            None,
            retry_on_status_codes=[],
            error=httpx.TimeoutException("timeout"),
        )
        == ResponseClassification.RETRYABLE
    )


def test_network_connect_error_is_retryable() -> None:
    assert (
        classify_http_outcome(
            None,
            retry_on_status_codes=[],
            error=httpx.ConnectError("refused"),
        )
        == ResponseClassification.RETRYABLE
    )


def test_custom_retry_on_status_codes_makes_404_retryable() -> None:
    outcome = classify_http_outcome(404, retry_on_status_codes=[404])
    assert outcome == ResponseClassification.RETRYABLE


def test_custom_retry_on_status_codes_other_4xx_still_poison() -> None:
    assert classify_http_outcome(400, retry_on_status_codes=[404]) == ResponseClassification.POISON


def test_custom_retry_on_status_codes_5xx_still_retryable() -> None:
    outcome = classify_http_outcome(503, retry_on_status_codes=[404])
    assert outcome == ResponseClassification.RETRYABLE

import math

import pytest

from app.domain.services.backoff import compute_delay_seconds


def test_attempt_1_base_delay_no_jitter() -> None:
    delay = compute_delay_seconds(1, rng=lambda: 0.5)
    assert delay == pytest.approx(30.0)


def test_attempt_2_exponential_growth() -> None:
    delay = compute_delay_seconds(2, rng=lambda: 0.5)
    assert delay == pytest.approx(60.0)


def test_attempt_capped_at_max_seconds() -> None:
    delay = compute_delay_seconds(20, base=30, multiplier=2, max_seconds=3600, rng=lambda: 0.5)
    assert delay == pytest.approx(3600.0)


def test_jitter_lower_bound() -> None:
    delay = compute_delay_seconds(1, base=30, jitter_pct=0.1, rng=lambda: 0.0)
    assert delay == pytest.approx(27.0)


def test_jitter_upper_bound() -> None:
    delay = compute_delay_seconds(1, base=30, jitter_pct=0.1, rng=lambda: 1.0)
    assert delay == pytest.approx(33.0)


def test_jitter_within_bounds() -> None:
    base_delay = min(30 * (2 ** (3 - 1)), 3600)
    low = base_delay * (1 - 0.1)
    high = base_delay * (1 + 0.1)
    for rng_value in (0.0, 0.25, 0.5, 0.75, 1.0):
        delay = compute_delay_seconds(3, rng=lambda v=rng_value: v)
        assert low <= delay <= high


def test_attempt_number_must_be_positive() -> None:
    with pytest.raises(ValueError, match="attempt_number"):
        compute_delay_seconds(0)


def test_default_policy_matches_spec() -> None:
    assert compute_delay_seconds(1, rng=lambda: 0.5) == pytest.approx(30.0)
    assert compute_delay_seconds(2, rng=lambda: 0.5) == pytest.approx(60.0)
    assert compute_delay_seconds(3, rng=lambda: 0.5) == pytest.approx(120.0)


def test_result_is_finite() -> None:
    delay = compute_delay_seconds(5, rng=lambda: 0.123)
    assert math.isfinite(delay)
    assert delay > 0

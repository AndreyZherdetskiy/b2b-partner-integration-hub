from collections.abc import Callable
from random import Random

_DEFAULT_BASE_SECONDS = 30
_DEFAULT_MULTIPLIER = 2
_DEFAULT_MAX_SECONDS = 3600
_DEFAULT_JITTER_PCT = 0.1


def compute_delay_seconds(
    attempt_number: int,
    *,
    base: int = _DEFAULT_BASE_SECONDS,
    multiplier: int = _DEFAULT_MULTIPLIER,
    max_seconds: int = _DEFAULT_MAX_SECONDS,
    jitter_pct: float = _DEFAULT_JITTER_PCT,
    rng: Callable[[], float] | None = None,
) -> float:
    if attempt_number < 1:
        msg = "attempt_number must be >= 1"
        raise ValueError(msg)

    delay_seconds = float(min(base * (multiplier ** (attempt_number - 1)), max_seconds))
    uniform = rng if rng is not None else Random().random
    jitter = delay_seconds * ((float(uniform()) * 2.0) - 1.0) * jitter_pct
    return delay_seconds + jitter

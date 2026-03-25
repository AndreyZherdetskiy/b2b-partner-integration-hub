from datetime import UTC, datetime, timedelta

from app.domain.services.sla_service import apply_first_success, deadline_passed_while_open


def _dt(seconds: float = 0.0) -> datetime:
    return datetime(2026, 3, 16, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds)


def test_first_success_sets_timestamp_when_empty() -> None:
    deadline = _dt(60)
    now = _dt(30)
    first_success, breached = apply_first_success(now, deadline, None, False)
    assert first_success == now
    assert breached is False


def test_first_success_within_sla_no_breach() -> None:
    deadline = _dt(60)
    now = _dt(59)
    first_success, breached = apply_first_success(now, deadline, None, False)
    assert first_success == now
    assert breached is False


def test_first_success_after_deadline_sets_breach_once() -> None:
    deadline = _dt(60)
    now = _dt(61)
    first_success, breached = apply_first_success(now, deadline, None, False)
    assert first_success == now
    assert breached is True


def test_second_success_does_not_change_first_success_at() -> None:
    deadline = _dt(60)
    original = _dt(30)
    later = _dt(90)
    first_success, breached = apply_first_success(later, deadline, original, False)
    assert first_success == original
    assert breached is False


def test_breach_not_flipped_when_already_breached() -> None:
    deadline = _dt(60)
    original = _dt(90)
    _, breached = apply_first_success(_dt(120), deadline, original, True)
    assert breached is True


def test_deadline_passed_while_open_flags_breach() -> None:
    deadline = _dt(60)
    now = _dt(61)
    assert deadline_passed_while_open(now, deadline, None, False) is True


def test_deadline_passed_while_open_ignores_delivered() -> None:
    deadline = _dt(60)
    now = _dt(120)
    assert deadline_passed_while_open(now, deadline, _dt(30), False) is False


def test_deadline_passed_while_open_only_once() -> None:
    deadline = _dt(60)
    now = _dt(120)
    assert deadline_passed_while_open(now, deadline, None, True) is False


def test_deadline_not_passed_while_open() -> None:
    deadline = _dt(60)
    now = _dt(30)
    assert deadline_passed_while_open(now, deadline, None, False) is False

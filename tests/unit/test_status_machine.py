import pytest

from app.domain.enums import DeliveryStatus
from app.domain.services.status_machine import can_transition, transition

VALID_TRANSITIONS: list[tuple[DeliveryStatus, DeliveryStatus]] = [
    (DeliveryStatus.PENDING, DeliveryStatus.DELIVERING),
    (DeliveryStatus.DELIVERING, DeliveryStatus.DELIVERED),
    (DeliveryStatus.DELIVERING, DeliveryStatus.RETRYING),
    (DeliveryStatus.DELIVERING, DeliveryStatus.FAILED),
    (DeliveryStatus.RETRYING, DeliveryStatus.DELIVERING),
    (DeliveryStatus.FAILED, DeliveryStatus.REPLAYING),
    (DeliveryStatus.REPLAYING, DeliveryStatus.DELIVERING),
    (DeliveryStatus.REPLAYING, DeliveryStatus.DELIVERED),
    (DeliveryStatus.REPLAYING, DeliveryStatus.FAILED),
]


@pytest.mark.parametrize(("src", "dst"), VALID_TRANSITIONS)
def test_valid_transitions(src: DeliveryStatus, dst: DeliveryStatus) -> None:
    assert can_transition(src, dst) is True
    assert transition(src, dst) == dst


INVALID_TRANSITIONS: list[tuple[DeliveryStatus, DeliveryStatus]] = [
    (DeliveryStatus.PENDING, DeliveryStatus.FAILED),
    (DeliveryStatus.PENDING, DeliveryStatus.DELIVERED),
    (DeliveryStatus.DELIVERED, DeliveryStatus.PENDING),
    (DeliveryStatus.DELIVERED, DeliveryStatus.DELIVERING),
    (DeliveryStatus.FAILED, DeliveryStatus.DELIVERING),
    (DeliveryStatus.RETRYING, DeliveryStatus.DELIVERED),
    (DeliveryStatus.REPLAYING, DeliveryStatus.RETRYING),
]


@pytest.mark.parametrize(("src", "dst"), INVALID_TRANSITIONS)
def test_invalid_transitions(src: DeliveryStatus, dst: DeliveryStatus) -> None:
    assert can_transition(src, dst) is False


def test_invalid_transition_raises_and_calls_on_invalid() -> None:
    calls: list[tuple[DeliveryStatus, DeliveryStatus]] = []

    def on_invalid(src: DeliveryStatus, dst: DeliveryStatus) -> None:
        calls.append((src, dst))

    with pytest.raises(ValueError, match="invalid transition"):
        transition(DeliveryStatus.PENDING, DeliveryStatus.FAILED, on_invalid=on_invalid)

    assert calls == [(DeliveryStatus.PENDING, DeliveryStatus.FAILED)]


def test_can_transition_same_status_is_false() -> None:
    for status in DeliveryStatus:
        assert can_transition(status, status) is False


def test_delivery_status_values() -> None:
    assert {s.value for s in DeliveryStatus} == {
        "pending",
        "delivering",
        "delivered",
        "retrying",
        "failed",
        "replaying",
    }

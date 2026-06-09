from collections.abc import Callable

from app.domain.enums import DeliveryStatus

_VALID_TRANSITIONS: frozenset[tuple[DeliveryStatus, DeliveryStatus]] = frozenset(
    {
        (DeliveryStatus.PENDING, DeliveryStatus.DELIVERING),
        (DeliveryStatus.DELIVERING, DeliveryStatus.DELIVERED),
        (DeliveryStatus.DELIVERING, DeliveryStatus.RETRYING),
        (DeliveryStatus.DELIVERING, DeliveryStatus.FAILED),
        (DeliveryStatus.RETRYING, DeliveryStatus.DELIVERING),
        (DeliveryStatus.FAILED, DeliveryStatus.REPLAYING),
        (DeliveryStatus.REPLAYING, DeliveryStatus.DELIVERING),
        (DeliveryStatus.REPLAYING, DeliveryStatus.DELIVERED),
        (DeliveryStatus.REPLAYING, DeliveryStatus.FAILED),
    }
)


def can_transition(src: DeliveryStatus, dst: DeliveryStatus) -> bool:
    return (src, dst) in _VALID_TRANSITIONS


def transition(
    src: DeliveryStatus,
    dst: DeliveryStatus,
    *,
    on_invalid: Callable[[DeliveryStatus, DeliveryStatus], None] | None = None,
) -> DeliveryStatus:
    if not can_transition(src, dst):
        if on_invalid is not None:
            on_invalid(src, dst)
        msg = f"invalid transition: {src.value} -> {dst.value}"
        raise ValueError(msg)
    return dst

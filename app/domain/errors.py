"""Domain errors raised by services; mapped to HTTP by the API layer."""

from __future__ import annotations


class HubError(Exception):
    """Base domain error with an HTTP status code and detail message."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class PartnerNotFoundError(HubError):
    def __init__(self) -> None:
        super().__init__(404, "Partner not found.")


class DeliveryNotFoundError(HubError):
    def __init__(self) -> None:
        super().__init__(404, "Delivery not found.")


class ReplayApprovalNotFoundError(HubError):
    def __init__(self) -> None:
        super().__init__(404, "Replay approval not found.")


class PartnerInactiveError(HubError):
    def __init__(self) -> None:
        super().__init__(422, "Partner is not active.")


class SchemaValidationFailedError(HubError):
    def __init__(self) -> None:
        super().__init__(422, "payload does not match registered schema")


class NoActiveEndpointError(HubError):
    def __init__(self, event_type: str) -> None:
        super().__init__(
            422,
            f"No active outbound endpoint for event_type: {event_type}",
        )


class IdempotencyConflictError(HubError):
    def __init__(self) -> None:
        super().__init__(409, "Idempotency conflict; retry.")


class DeliveryNotReplayableError(HubError):
    def __init__(self, current_status: str) -> None:
        super().__init__(
            409,
            f"Delivery cannot be replayed from status: {current_status}.",
        )


class ReplayApprovalNotPendingError(HubError):
    def __init__(self, current_status: str) -> None:
        super().__init__(
            409,
            f"Replay approval is not pending: {current_status}.",
        )

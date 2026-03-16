from enum import StrEnum


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    RETRYING = "retrying"
    FAILED = "failed"
    REPLAYING = "replaying"


class PartnerStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    PROVISIONING = "provisioning"


class EndpointDirection(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class EndpointStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


class DeliveryDirection(StrEnum):
    OUTBOUND = "outbound"


class DeadLetterReason(StrEnum):
    MAX_ATTEMPTS_EXCEEDED = "max_attempts_exceeded"
    NON_RETRYABLE_ERROR = "non_retryable_error"
    MANUAL_PURGE = "manual_purge"


class HubRole(StrEnum):
    HUB_ADMIN = "hub_admin"
    HUB_OPERATOR = "hub_operator"
    HUB_VIEWER = "hub_viewer"


class SigningSecretStatus(StrEnum):
    PRIMARY = "primary"
    PREVIOUS = "previous"
    REVOKED = "revoked"


class PayloadSchemaStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class ReplayApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

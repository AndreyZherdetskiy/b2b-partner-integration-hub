"""ORM model registry for Alembic autogenerate and imports."""

from app.db.base import Base
from app.domain.models.api_key import PartnerApiKey
from app.domain.models.attempt import DeliveryAttempt
from app.domain.models.audit import AuditLog
from app.domain.models.dead_letter import DeadLetter
from app.domain.models.delivery import Delivery
from app.domain.models.endpoint import PartnerEndpoint
from app.domain.models.inbound_event import InboundEvent
from app.domain.models.outbox import OutboxEvent
from app.domain.models.partner import Partner
from app.domain.models.payload_schema import PayloadSchema
from app.domain.models.replay_approval import ReplayApproval
from app.domain.models.signing_secret import PartnerSigningSecret

__all__ = [
    "Base",
    "AuditLog",
    "DeadLetter",
    "Delivery",
    "DeliveryAttempt",
    "InboundEvent",
    "OutboxEvent",
    "Partner",
    "PartnerApiKey",
    "PartnerEndpoint",
    "PartnerSigningSecret",
    "PayloadSchema",
    "ReplayApproval",
]

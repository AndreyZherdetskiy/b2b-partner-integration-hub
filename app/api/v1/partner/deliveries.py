"""Partner read-only delivery status API (spec §3.5)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.deps_partner import PartnerAuth
from app.domain.enums import DeliveryStatus
from app.domain.models.delivery import Delivery
from app.schemas.partner_delivery import PartnerDeliveryStatusResponse, PartnerErrorResponse

router = APIRouter(prefix="/partner/v1", tags=["partner"])

_PARTNER_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": PartnerErrorResponse, "description": "Missing or invalid API key."},
    403: {
        "model": PartnerErrorResponse,
        "description": "API key lacks status:read scope.",
    },
    404: {"model": PartnerErrorResponse, "description": "Delivery not found."},
}


def _to_status_response(delivery: Delivery) -> PartnerDeliveryStatusResponse:
    return PartnerDeliveryStatusResponse(
        id=delivery.public_id,
        status=DeliveryStatus(delivery.status),
        event_type=delivery.event_type,
        attempt_count=delivery.attempt_count,
        last_error_code=delivery.last_error_code,
        sla_breached=delivery.sla_breached,
        first_success_at=delivery.first_success_at,
    )


@router.get(
    "/deliveries/{delivery_id}",
    summary="Get delivery status",
    description=(
        "Read-only status for a delivery owned by the authenticated partner. "
        "`delivery_id` is the public UUIDv7. Webhook payload is never returned. "
        "Another partner's delivery returns **404**."
    ),
    response_model=PartnerDeliveryStatusResponse,
    responses=_PARTNER_ERRORS,
)
async def get_delivery_status(
    session: DbSession,
    auth: PartnerAuth,
    delivery_id: Annotated[
        uuid.UUID,
        Path(..., description="Delivery public UUIDv7 identifier."),
    ],
) -> PartnerDeliveryStatusResponse:
    result = await session.execute(select(Delivery).where(Delivery.public_id == delivery_id))
    delivery = result.scalar_one_or_none()
    if delivery is None or delivery.partner_id != auth.partner_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delivery not found.",
        )
    return _to_status_response(delivery)

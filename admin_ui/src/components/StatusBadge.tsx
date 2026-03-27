import type { DeliveryStatus } from "../api/client";
import { deliveryStatusClass, deliveryStatusLabel } from "./DeliveryStatusBadge";

interface Props {
  status: DeliveryStatus;
}

export function DeliveryStatusBadge({ status }: Props) {
  return (
    <span className={`badge ${deliveryStatusClass(status)}`}>
      {deliveryStatusLabel(status)}
    </span>
  );
}

import type { DeliveryStatus } from "../api/client";

const STATUS_LABEL: Record<DeliveryStatus, string> = {
  pending: "Pending",
  delivering: "Delivering",
  delivered: "Delivered",
  retrying: "Retrying",
  failed: "Failed",
  replaying: "Replaying",
};

export function deliveryStatusLabel(status: DeliveryStatus): string {
  switch (status) {
    case "pending":
    case "delivering":
    case "delivered":
    case "retrying":
    case "failed":
    case "replaying":
      return STATUS_LABEL[status];
    default: {
      const _exhaustive: never = status;
      return _exhaustive;
    }
  }
}

export function deliveryStatusClass(status: DeliveryStatus): string {
  switch (status) {
    case "pending":
      return "status-pending";
    case "delivering":
      return "status-delivering";
    case "delivered":
      return "status-delivered";
    case "retrying":
      return "status-retrying";
    case "failed":
      return "status-failed";
    case "replaying":
      return "status-replaying";
    default: {
      const _exhaustive: never = status;
      return _exhaustive;
    }
  }
}

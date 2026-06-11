import type { ReplayApprovalStatus } from "../api/client";

const STATUS_LABEL: Record<ReplayApprovalStatus, string> = {
  pending: "Pending",
  approved: "Approved",
  rejected: "Rejected",
};

export function replayApprovalStatusLabel(status: ReplayApprovalStatus): string {
  switch (status) {
    case "pending":
    case "approved":
    case "rejected":
      return STATUS_LABEL[status];
    default: {
      const _exhaustive: never = status;
      return _exhaustive;
    }
  }
}

export function replayApprovalStatusClass(status: ReplayApprovalStatus): string {
  switch (status) {
    case "pending":
      return "approval-pending";
    case "approved":
      return "approval-approved";
    case "rejected":
      return "approval-rejected";
    default: {
      const _exhaustive: never = status;
      return _exhaustive;
    }
  }
}

interface Props {
  status: ReplayApprovalStatus;
}

export function ReplayApprovalStatusBadge({ status }: Props) {
  return (
    <span className={`badge ${replayApprovalStatusClass(status)}`}>
      {replayApprovalStatusLabel(status)}
    </span>
  );
}

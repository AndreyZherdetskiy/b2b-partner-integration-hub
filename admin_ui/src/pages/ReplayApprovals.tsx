import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  approveReplayApproval,
  listReplayApprovals,
  rejectReplayApproval,
  type ReplayApprovalItem,
  type ReplayApprovalStatus,
} from "../api/client";
import { ErrorBanner } from "../components/ErrorBanner";
import { ReplayApprovalStatusBadge } from "../components/ReplayApprovalStatusBadge";

const PAGE_SIZE = 25;
const STATUSES: ReplayApprovalStatus[] = ["pending", "approved", "rejected"];

interface Props {
  onSelectDelivery: (id: string) => void;
}

export function ReplayApprovals({ onSelectDelivery }: Props) {
  const [status, setStatus] = useState<ReplayApprovalStatus>("pending");
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<ReplayApprovalItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [rejectReason, setRejectReason] = useState<Record<string, string>>({});
  const [acting, setActing] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listReplayApprovals({
        status,
        limit: PAGE_SIZE,
        offset,
      });
      setItems(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load approvals");
    } finally {
      setLoading(false);
    }
  }, [status, offset]);

  useEffect(() => {
    load();
  }, [load]);

  function applyStatus(next: ReplayApprovalStatus) {
    setStatus(next);
    setOffset(0);
  }

  async function handleApprove(id: string) {
    setActing(id);
    setActionError("");
    try {
      await approveReplayApproval(id);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Approve failed");
    } finally {
      setActing(null);
    }
  }

  async function handleReject(id: string) {
    const trimmed = (rejectReason[id] ?? "").trim();
    if (!trimmed) {
      return;
    }
    setActing(id);
    setActionError("");
    try {
      await rejectReplayApproval(id, { reason: trimmed });
      setRejectReason((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Reject failed");
    } finally {
      setActing(null);
    }
  }

  return (
    <section>
      <h1>Replay approvals</h1>
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      <ErrorBanner message={actionError} onDismiss={() => setActionError("")} />

      <div className="filters">
        <label>
          Status
          <select
            value={status}
            onChange={(e) => applyStatus(e.target.value as ReplayApprovalStatus)}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
      </div>

      {loading ? <p>Loading…</p> : null}

      <table className="data-table">
        <thead>
          <tr>
            <th>Approval ID</th>
            <th>Delivery</th>
            <th>Status</th>
            <th>Reason</th>
            <th>Requested by</th>
            <th>Created</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const rejectTrimmed = (rejectReason[item.id] ?? "").trim();
            const rejectDisabled = rejectTrimmed.length === 0 || acting === item.id;
            return (
              <tr key={item.id}>
                <td className="mono">{item.id}</td>
                <td>
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => onSelectDelivery(item.delivery_id)}
                  >
                    {item.delivery_id}
                  </button>
                </td>
                <td><ReplayApprovalStatusBadge status={item.status} /></td>
                <td>{item.reason}</td>
                <td>{item.requested_by}</td>
                <td>{new Date(item.created_at).toLocaleString()}</td>
                <td>
                  {item.status === "pending" ? (
                    <div className="inline-actions">
                      <button
                        type="button"
                        disabled={acting === item.id}
                        onClick={() => handleApprove(item.id)}
                      >
                        Approve
                      </button>
                      <input
                        value={rejectReason[item.id] ?? ""}
                        onChange={(e) =>
                          setRejectReason((prev) => ({ ...prev, [item.id]: e.target.value }))
                        }
                        placeholder="Reject reason"
                      />
                      <button
                        type="button"
                        disabled={rejectDisabled}
                        onClick={() => handleReject(item.id)}
                      >
                        Reject
                      </button>
                    </div>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="pagination">
        <button
          type="button"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
        >
          Previous
        </button>
        <span>
          {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`} of {total}
        </span>
        <button
          type="button"
          disabled={offset + PAGE_SIZE >= total}
          onClick={() => setOffset(offset + PAGE_SIZE)}
        >
          Next
        </button>
      </div>
    </section>
  );
}

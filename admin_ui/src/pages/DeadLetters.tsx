import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  ackDeadLetter,
  listDeadLetters,
  purgeDeadLetter,
  type DeadLetter,
} from "../api/client";
import { ErrorBanner } from "../components/ErrorBanner";

const PAGE_SIZE = 25;

interface Props {
  onSelectDelivery: (id: string) => void;
}

export function DeadLetters({ onSelectDelivery }: Props) {
  const [items, setItems] = useState<DeadLetter[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [purgeReason, setPurgeReason] = useState<Record<string, string>>({});
  const [acting, setActing] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listDeadLetters(PAGE_SIZE, offset);
      setItems(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load dead letters");
    } finally {
      setLoading(false);
    }
  }, [offset]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleAck(id: string) {
    setActing(id);
    setActionError("");
    try {
      await ackDeadLetter(id);
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Acknowledge failed");
    } finally {
      setActing(null);
    }
  }

  async function handlePurge(id: string) {
    const trimmed = (purgeReason[id] ?? "").trim();
    if (!trimmed) {
      return;
    }
    setActing(id);
    setActionError("");
    try {
      await purgeDeadLetter(id, { reason: trimmed });
      setPurgeReason((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      await load();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Purge failed");
    } finally {
      setActing(null);
    }
  }

  return (
    <section>
      <h1>Dead letters</h1>
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      <ErrorBanner message={actionError} onDismiss={() => setActionError("")} />

      {loading ? <p>Loading…</p> : null}

      <table className="data-table">
        <thead>
          <tr>
            <th>Reason</th>
            <th>Created</th>
            <th>Delivery</th>
            <th>Last HTTP</th>
            <th>Message</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row) => {
            const isAcked = row.acknowledged_at !== null;
            const reason = purgeReason[row.id] ?? "";
            const purgeDisabled = reason.trim().length === 0 || acting === row.id;
            return (
              <tr key={row.id}>
                <td>{row.reason}</td>
                <td>{new Date(row.created_at).toLocaleString()}</td>
                <td>
                  <button
                    type="button"
                    className="link-button mono"
                    onClick={() => onSelectDelivery(row.delivery_id)}
                  >
                    {row.delivery_id}
                  </button>
                </td>
                <td>{row.last_http_status ?? "—"}</td>
                <td>{row.last_error_message}</td>
                <td>{isAcked ? "acked" : "open"}</td>
                <td className="dlq-actions">
                  {!isAcked ? (
                    <button
                      type="button"
                      disabled={acting === row.id}
                      onClick={() => handleAck(row.id)}
                    >
                      {acting === row.id ? "…" : "Ack"}
                    </button>
                  ) : null}
                  <label className="purge-inline">
                    <input
                      type="text"
                      value={reason}
                      placeholder="Purge reason"
                      onChange={(e) =>
                        setPurgeReason((prev) => ({ ...prev, [row.id]: e.target.value }))
                      }
                    />
                    <button
                      type="button"
                      disabled={purgeDisabled}
                      onClick={() => handlePurge(row.id)}
                    >
                      Purge
                    </button>
                  </label>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="pagination">
        <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
          Previous
        </button>
        <span>
          {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
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

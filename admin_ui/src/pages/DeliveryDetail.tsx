import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  getDelivery,
  replayDelivery,
  type Delivery,
} from "../api/client";
import { DeliveryStatusBadge } from "../components/StatusBadge";
import { ErrorBanner } from "../components/ErrorBanner";
import { copyText, formatPayload } from "../utils/display";

interface Props {
  id: string;
  onBack: () => void;
}

export function DeliveryDetail({ id, onBack }: Props) {
  const [delivery, setDelivery] = useState<Delivery | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [replayError, setReplayError] = useState("");
  const [replaySuccess, setReplaySuccess] = useState("");
  const [reason, setReason] = useState("");
  const [resetCounter, setResetCounter] = useState(false);
  const [replaying, setReplaying] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await getDelivery(id);
      setDelivery(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load delivery");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCopyId() {
    await copyText(id);
  }

  async function handleReplay() {
    const trimmed = reason.trim();
    if (!trimmed) {
      return;
    }
    setReplaying(true);
    setReplayError("");
    setReplaySuccess("");
    try {
      const result = await replayDelivery(id, {
        reason: trimmed,
        reset_attempt_counter: resetCounter,
      });
      if ("approval_id" in result) {
        setReplaySuccess(
          `Queued for approval — approval ${result.approval_id} (status: ${result.status})`,
        );
      } else {
        setReplaySuccess(`Replay accepted — status is now ${result.status}`);
        await load();
      }
    } catch (err) {
      setReplayError(err instanceof ApiError ? err.message : "Replay failed");
    } finally {
      setReplaying(false);
    }
  }

  const replayDisabled = reason.trim().length === 0 || replaying;

  if (loading) {
    return <p>Loading…</p>;
  }

  if (!delivery) {
    return (
      <section>
        <ErrorBanner message={error} />
        <button type="button" onClick={onBack}>Back to list</button>
      </section>
    );
  }

  return (
    <section>
      <button type="button" className="back-link" onClick={onBack}>← Deliveries</button>
      <h1>Delivery</h1>
      <ErrorBanner message={error} onDismiss={() => setError("")} />

      <div className="detail-grid">
        <div>
          <h2>Summary</h2>
          <dl className="meta-list">
            <dt>Public ID</dt>
            <dd className="mono">
              {delivery.id}
              <button type="button" className="link-button" onClick={() => handleCopyId()}>Copy</button>
            </dd>
            <dt>Partner ID</dt>
            <dd className="mono">{delivery.partner_id}</dd>
            <dt>Status</dt>
            <dd><DeliveryStatusBadge status={delivery.status} /></dd>
            <dt>Event type</dt>
            <dd>{delivery.event_type}</dd>
            <dt>Correlation</dt>
            <dd className="mono">{delivery.correlation_id}</dd>
            <dt>Attempts</dt>
            <dd>{delivery.attempt_count} / {delivery.max_attempts}</dd>
            <dt>SLA breached</dt>
            <dd>{delivery.sla_breached ? "yes" : "no"}</dd>
            {delivery.last_error_message ? (
              <>
                <dt>Last error</dt>
                <dd>{delivery.last_error_message}</dd>
              </>
            ) : null}
          </dl>
        </div>

        <div>
          <h2>Payload (masked)</h2>
          <pre className="payload-block">{formatPayload(delivery.payload)}</pre>
        </div>
      </div>

      <div className="attempts-section">
        <h2>Attempts</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Requested</th>
              <th>HTTP</th>
              <th>Duration</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {delivery.attempts.map((a) => (
              <tr key={a.id}>
                <td>{a.attempt_number}</td>
                <td>{new Date(a.requested_at).toLocaleString()}</td>
                <td>{a.http_status_code ?? "—"}</td>
                <td>{a.duration_ms != null ? `${a.duration_ms} ms` : "—"}</td>
                <td>{a.error_type ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="replay-form">
        {/* POST /admin/v1/deliveries/{id}/replay */}
        <h2>Replay</h2>
        <ErrorBanner message={replayError} onDismiss={() => setReplayError("")} />
        {replaySuccess ? <p className="success-text">{replaySuccess}</p> : null}
        <label>
          Reason (required)
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            placeholder="Operator reason for audit trail"
          />
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={resetCounter}
            onChange={(e) => setResetCounter(e.target.checked)}
          />
          Reset attempt counter
        </label>
        <button type="button" disabled={replayDisabled} onClick={handleReplay}>
          {replaying ? "Replaying…" : "Replay delivery"}
        </button>
      </div>
    </section>
  );
}

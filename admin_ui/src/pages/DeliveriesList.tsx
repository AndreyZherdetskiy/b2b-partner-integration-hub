import { useCallback, useEffect, useState } from "react";
import {
  type Delivery,
  type DeliveryStatus,
  listDeliveries,
  bulkReplayDeliveries,
  ApiError,
} from "../api/client";
import { DeliveryStatusBadge } from "../components/StatusBadge";
import { ErrorBanner } from "../components/ErrorBanner";

const STATUSES: DeliveryStatus[] = [
  "pending",
  "delivering",
  "delivered",
  "retrying",
  "failed",
  "replaying",
];

const PAGE_SIZE = 25;
const BULK_CAP = 100;

interface Props {
  onSelect: (id: string) => void;
}

export function DeliveriesList({ onSelect }: Props) {
  const [partnerId, setPartnerId] = useState("");
  const [status, setStatus] = useState<DeliveryStatus | "">("");
  const [eventType, setEventType] = useState("");
  const [slaBreached, setSlaBreached] = useState<"" | "true" | "false">("");
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<Delivery[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkReason, setBulkReason] = useState("");
  const [bulkLoading, setBulkLoading] = useState(false);
  const [bulkError, setBulkError] = useState("");
  const [bulkResult, setBulkResult] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listDeliveries({
        partner_id: partnerId.trim() || undefined,
        status: status || undefined,
        event_type: eventType.trim() || undefined,
        sla_breached: slaBreached === "" ? undefined : slaBreached === "true",
        limit: PAGE_SIZE,
        offset,
      });
      setItems(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load deliveries");
    } finally {
      setLoading(false);
    }
  }, [partnerId, status, eventType, slaBreached, offset]);

  useEffect(() => {
    load();
  }, [load]);

  function applyFilters() {
    if (offset === 0) {
      load();
    } else {
      setOffset(0);
    }
  }

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else if (next.size < BULK_CAP) {
        next.add(id);
      }
      return next;
    });
  }

  function toggleSelectAll() {
    if (selected.size === items.length) {
      setSelected(new Set());
    } else {
      const capped = items.slice(0, BULK_CAP).map((d) => d.id);
      setSelected(new Set(capped));
    }
  }

  async function handleBulkReplay() {
    const trimmed = bulkReason.trim();
    if (!trimmed || selected.size === 0) {
      return;
    }
    setBulkLoading(true);
    setBulkError("");
    setBulkResult("");
    try {
      const result = await bulkReplayDeliveries({
        delivery_ids: [...selected],
        reason: trimmed,
      });
      const skipped =
        result.skipped_open_circuit.length +
        result.skipped_rate_limited.length +
        result.skipped_invalid_status.length +
        result.not_found.length;
      setBulkResult(
        `Replayed ${result.replayed.length}, skipped ${skipped} (of ${result.requested.length} requested)`,
      );
      setSelected(new Set());
      await load();
    } catch (err) {
      setBulkError(err instanceof ApiError ? err.message : "Bulk replay failed");
    } finally {
      setBulkLoading(false);
    }
  }

  const bulkDisabled =
    bulkReason.trim().length === 0 || selected.size === 0 || bulkLoading;

  return (
    <section>
      <h1>Deliveries</h1>
      <ErrorBanner message={error} onDismiss={() => setError("")} />

      <div className="filters">
        <label>
          Partner ID
          <input value={partnerId} onChange={(e) => setPartnerId(e.target.value)} />
        </label>
        <label>
          Status
          <select value={status} onChange={(e) => setStatus(e.target.value as DeliveryStatus | "")}>
            <option value="">Any</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <label>
          Event type
          <input value={eventType} onChange={(e) => setEventType(e.target.value)} />
        </label>
        <label>
          SLA breached
          <select value={slaBreached} onChange={(e) => setSlaBreached(e.target.value as "" | "true" | "false")}>
            <option value="">Any</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </label>
        <button type="button" onClick={applyFilters}>Apply</button>
      </div>

      {loading ? <p>Loading…</p> : null}

      <div className="bulk-replay-form">
        <h2>Bulk replay</h2>
        <ErrorBanner message={bulkError} onDismiss={() => setBulkError("")} />
        {bulkResult ? <p className="success-text">{bulkResult}</p> : null}
        <label>
          Reason (required)
          <textarea
            value={bulkReason}
            onChange={(e) => setBulkReason(e.target.value)}
            rows={2}
            placeholder="Operator reason for audit trail"
          />
        </label>
        <p className="hint">
          {selected.size} selected (max {BULK_CAP})
        </p>
        <button type="button" disabled={bulkDisabled} onClick={handleBulkReplay}>
          {bulkLoading ? "Replaying…" : "Bulk replay selected"}
        </button>
      </div>

      <table className="data-table">
        <thead>
          <tr>
            <th>
              <input
                type="checkbox"
                checked={items.length > 0 && selected.size === items.length}
                onChange={toggleSelectAll}
                aria-label="Select all on page"
              />
            </th>
            <th>ID</th>
            <th>Partner</th>
            <th>Event</th>
            <th>Status</th>
            <th>SLA</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {items.map((d) => (
            <tr key={d.id}>
              <td>
                <input
                  type="checkbox"
                  checked={selected.has(d.id)}
                  disabled={!selected.has(d.id) && selected.size >= BULK_CAP}
                  onChange={() => toggleSelect(d.id)}
                  aria-label={`Select ${d.id}`}
                />
              </td>
              <td>
                <button type="button" className="link-button" onClick={() => onSelect(d.id)}>
                  {d.id}
                </button>
              </td>
              <td className="mono">{d.partner_id}</td>
              <td>{d.event_type}</td>
              <td><DeliveryStatusBadge status={d.status} /></td>
              <td>{d.sla_breached ? "breached" : "ok"}</td>
              <td>{new Date(d.created_at).toLocaleString()}</td>
            </tr>
          ))}
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

import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  listPartners,
  sandboxTestDelivery,
  type Partner,
} from "../api/client";
import { ErrorBanner } from "../components/ErrorBanner";

const DEFAULT_PAYLOAD = '{\n  "order_id": "ord_sandbox_1"\n}';

export function Sandbox() {
  const [partners, setPartners] = useState<Partner[]>([]);
  const [partnerId, setPartnerId] = useState("");
  const [eventType, setEventType] = useState("order.created");
  const [payloadText, setPayloadText] = useState(DEFAULT_PAYLOAD);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [result, setResult] = useState("");

  useEffect(() => {
    listPartners(200, 0)
      .then((res) => setPartners(res.items))
      .catch((err: unknown) => {
        setLoadError(err instanceof ApiError ? err.message : "Failed to load partners");
      });
  }, []);

  const parsedPayload = useMemo(() => {
    try {
      const value: unknown = JSON.parse(payloadText);
      if (value !== null && typeof value === "object" && !Array.isArray(value)) {
        return value as Record<string, unknown>;
      }
      return null;
    } catch {
      return null;
    }
  }, [payloadText]);

  const canSubmit =
    partnerId.trim().length > 0 &&
    eventType.trim().length > 0 &&
    parsedPayload !== null &&
    !loading;

  async function handleSubmit() {
    if (!canSubmit || parsedPayload === null) {
      return;
    }
    setLoading(true);
    setSubmitError("");
    setResult("");
    try {
      const response = await sandboxTestDelivery({
        partner_id: partnerId.trim(),
        event_type: eventType.trim(),
        payload: parsedPayload,
      });
      setResult(
        `Status: ${response.status}. Delivery IDs: ${response.delivery_ids.join(", ")}`,
      );
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : "Sandbox submit failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <h1>Sandbox</h1>
      <p className="hint">POST /admin/v1/deliveries/test — enqueue a test outbound delivery.</p>
      <ErrorBanner message={loadError} onDismiss={() => setLoadError("")} />
      <ErrorBanner message={submitError} onDismiss={() => setSubmitError("")} />
      {result ? <p className="success-text">{result}</p> : null}

      <div className="filters">
        <label>
          Partner
          <select
            value={partnerId}
            onChange={(e) => setPartnerId(e.target.value)}
          >
            <option value="">Select partner…</option>
            {partners.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.slug}) — {p.id}
              </option>
            ))}
          </select>
        </label>
        <label>
          Partner ID (paste UUID)
          <input
            value={partnerId}
            onChange={(e) => setPartnerId(e.target.value)}
            placeholder="Partner public UUIDv7"
          />
        </label>
        <label>
          Event type
          <input
            value={eventType}
            onChange={(e) => setEventType(e.target.value)}
            placeholder="order.created"
          />
        </label>
        <label>
          Payload (JSON object)
          <textarea
            value={payloadText}
            onChange={(e) => setPayloadText(e.target.value)}
            rows={8}
            className="payload-block"
          />
        </label>
        {parsedPayload === null ? (
          <p className="hint">Payload must be valid JSON object.</p>
        ) : null}
        <button type="button" disabled={!canSubmit} onClick={handleSubmit}>
          {loading ? "Submitting…" : "Submit test delivery"}
        </button>
      </div>
    </section>
  );
}

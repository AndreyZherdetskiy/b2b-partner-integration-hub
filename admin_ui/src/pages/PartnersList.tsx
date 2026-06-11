import { useCallback, useEffect, useState } from "react";
import { ApiError, listPartners, type Partner } from "../api/client";
import { ErrorBanner } from "../components/ErrorBanner";

const PAGE_SIZE = 25;

interface Props {
  onSelectCompliance: (id: string) => void;
}

export function PartnersList({ onSelectCompliance }: Props) {
  const [items, setItems] = useState<Partner[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listPartners(PAGE_SIZE, offset);
      setItems(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load partners");
    } finally {
      setLoading(false);
    }
  }, [offset]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section>
      <h1>Partners</h1>
      <ErrorBanner message={error} onDismiss={() => setError("")} />

      {loading ? <p>Loading…</p> : null}

      <table className="data-table">
        <thead>
          <tr>
            <th>Slug</th>
            <th>Status</th>
            <th>SLA (s)</th>
            <th>Public ID</th>
            <th>Compliance</th>
          </tr>
        </thead>
        <tbody>
          {items.map((p) => (
            <tr key={p.id}>
              <td>{p.slug}</td>
              <td>{p.status}</td>
              <td>{p.sla_seconds}</td>
              <td className="mono">{p.id}</td>
              <td>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => onSelectCompliance(p.id)}
                >
                  View compliance
                </button>
              </td>
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

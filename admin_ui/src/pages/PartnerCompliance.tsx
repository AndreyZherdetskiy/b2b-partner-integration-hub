import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  getPartnerAnalyticsSummary,
  type PartnerAnalyticsSummary,
} from "../api/client";
import { BreakerStateBadge } from "../components/BreakerStateBadge";
import { ErrorBanner } from "../components/ErrorBanner";

interface Props {
  partnerId: string;
  onBack: () => void;
}

function formatPercent(value: number | null): string {
  if (value === null) {
    return "—";
  }
  return `${(value * 100).toFixed(1)}%`;
}

function formatPct(value: number | null): string {
  if (value === null) {
    return "—";
  }
  return `${value.toFixed(1)}%`;
}

function formatDuration(seconds: number): string {
  if (seconds === 0) {
    return "—";
  }
  if (seconds < 60) {
    return `${seconds}s`;
  }
  if (seconds < 3600) {
    return `${Math.floor(seconds / 60)}m`;
  }
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

export function PartnerCompliance({ partnerId, onBack }: Props) {
  const [summary, setSummary] = useState<PartnerAnalyticsSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await getPartnerAnalyticsSummary(partnerId);
      setSummary(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load compliance summary");
    } finally {
      setLoading(false);
    }
  }, [partnerId]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return <p>Loading…</p>;
  }

  if (!summary) {
    return (
      <section>
        <ErrorBanner message={error} />
        <button type="button" onClick={onBack}>Back to partners</button>
      </section>
    );
  }

  return (
    <section>
      <button type="button" className="back-link" onClick={onBack}>← Partners</button>
      <h1>Partner compliance</h1>
      <ErrorBanner message={error} onDismiss={() => setError("")} />

      <dl className="meta-list">
        <dt>Slug</dt>
        <dd>{summary.slug}</dd>
        <dt>Window</dt>
        <dd>{summary.window_hours} hours</dd>
        <dt>Success rate</dt>
        <dd>{formatPercent(summary.success_rate)}</dd>
        <dt>P95 latency</dt>
        <dd>{summary.p95_latency_ms != null ? `${summary.p95_latency_ms} ms` : "—"}</dd>
        <dt>SLA compliance</dt>
        <dd>{formatPct(summary.sla_compliance_pct)}</dd>
        <dt>SLA breaches</dt>
        <dd>{summary.sla_breaches}</dd>
        <dt>Circuit state</dt>
        <dd><BreakerStateBadge state={summary.circuit_state} /></dd>
        <dt>DLQ age</dt>
        <dd>{formatDuration(summary.dlq_age_seconds)}</dd>
      </dl>
    </section>
  );
}

export const API_BASE = "http://localhost:8000";

export const TOKEN_KEY = "hub_admin_token";

export type DeliveryStatus =
  | "pending"
  | "delivering"
  | "delivered"
  | "retrying"
  | "failed"
  | "replaying";

export type PartnerStatus = "active" | "suspended" | "provisioning";

export type BreakerState = "closed" | "open" | "half_open" | "unknown";

export interface Paginated<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface DeliveryAttempt {
  id: string;
  attempt_number: number;
  requested_at: string;
  responded_at: string | null;
  http_status_code: number | null;
  error_type: string | null;
  duration_ms: number | null;
  created_at: string;
}

export interface Delivery {
  id: string;
  partner_id: string;
  endpoint_id: string;
  event_type: string;
  status: DeliveryStatus;
  attempt_count: number;
  max_attempts: number;
  idempotency_key: string;
  payload: Record<string, unknown>;
  correlation_id: string;
  sla_deadline_at: string;
  sla_breached: boolean;
  first_success_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  created_at: string;
  updated_at: string;
  attempts: DeliveryAttempt[];
}

export interface DeadLetter {
  id: string;
  delivery_id: string;
  partner_id: string;
  reason: string;
  last_http_status: number | null;
  last_error_message: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  created_at: string;
}

export interface Partner {
  id: string;
  slug: string;
  name: string;
  status: PartnerStatus;
  sla_seconds: number;
  rate_limit_rps: number;
  auto_replay_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ReplayResponse {
  delivery_id: string;
  status: DeliveryStatus;
}

export type ReplayApprovalStatus = "pending" | "approved" | "rejected";

export interface ReplayApprovalPendingResponse {
  approval_id: string;
  status: "pending";
}

export interface ReplayApprovalItem {
  id: string;
  delivery_id: string;
  reason: string;
  requested_by: string;
  approved_by: string | null;
  status: ReplayApprovalStatus;
  created_at: string;
}

export interface SandboxTestResponse {
  delivery_id: string;
  delivery_ids: string[];
  status: "accepted" | "duplicate";
}

export interface BulkReplayResponse {
  requested: string[];
  replayed: string[];
  skipped_open_circuit: string[];
  skipped_rate_limited: string[];
  skipped_invalid_status: string[];
  not_found: string[];
}

export interface DeadLetterAckResponse {
  id: string;
  acknowledged_at: string;
  acknowledged_by: string;
}

export interface DeadLetterPurgeResponse {
  id: string;
  reason: string;
}

export interface PartnerAnalyticsSummary {
  id: string;
  slug: string;
  window_hours: number;
  success_rate: number | null;
  p95_latency_ms: number | null;
  sla_compliance_pct: number | null;
  sla_breaches: number;
  circuit_state: BreakerState;
  dlq_age_seconds: number;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function getStoredToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) ?? "";
}

export function setStoredToken(token: string): void {
  if (token.trim()) {
    sessionStorage.setItem(TOKEN_KEY, token.trim());
  } else {
    sessionStorage.removeItem(TOKEN_KEY);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getStoredToken();
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type") && init?.body) {
    headers.set("Content-Type", "application/json");
  }
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) {
        detail = body.detail;
      }
    } catch {
      // non-JSON error body
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export interface DeliveryListParams {
  partner_id?: string;
  status?: DeliveryStatus;
  event_type?: string;
  sla_breached?: boolean;
  limit?: number;
  offset?: number;
}

function toQuery(params: object): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

export function listDeliveries(params: DeliveryListParams = {}): Promise<Paginated<Delivery>> {
  return request(`/admin/v1/deliveries${toQuery(params)}`);
}

export function replayDelivery(
  id: string,
  body: { reason: string; reset_attempt_counter: boolean },
): Promise<ReplayResponse | ReplayApprovalPendingResponse> {
  const token = getStoredToken();
  const headers = new Headers({ "Content-Type": "application/json" });
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return fetch(`${API_BASE}/admin/v1/deliveries/${id}/replay`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  }).then(async (response) => {
    const data = (await response.json()) as ReplayResponse | ReplayApprovalPendingResponse | { detail?: string };
    if (!response.ok) {
      const detail = "detail" in data && data.detail ? data.detail : response.statusText;
      throw new ApiError(detail, response.status);
    }
    return data as ReplayResponse | ReplayApprovalPendingResponse;
  });
}

export function bulkReplayDeliveries(
  body: { delivery_ids: string[]; reason: string },
): Promise<BulkReplayResponse> {
  return request("/admin/v1/deliveries/bulk-replay", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getDelivery(id: string): Promise<Delivery> {
  return request(`/admin/v1/deliveries/${id}`);
}

export function listDeadLetters(limit = 50, offset = 0): Promise<Paginated<DeadLetter>> {
  return request(`/admin/v1/dead-letters${toQuery({ limit, offset })}`);
}

export function ackDeadLetter(id: string): Promise<DeadLetterAckResponse> {
  return request(`/admin/v1/dead-letters/${id}/ack`, { method: "POST" });
}

export function purgeDeadLetter(
  id: string,
  body: { reason: string },
): Promise<DeadLetterPurgeResponse> {
  return request(`/admin/v1/dead-letters/${id}`, {
    method: "DELETE",
    body: JSON.stringify(body),
  });
}

export function listPartners(limit = 50, offset = 0): Promise<Paginated<Partner>> {
  return request(`/admin/v1/partners${toQuery({ limit, offset })}`);
}

export function getPartnerAnalyticsSummary(partnerId: string): Promise<PartnerAnalyticsSummary> {
  return request(`/admin/v1/analytics/partners/${partnerId}/summary`);
}

export function listReplayApprovals(
  params: { status?: ReplayApprovalStatus; limit?: number; offset?: number } = {},
): Promise<Paginated<ReplayApprovalItem>> {
  return request(`/admin/v1/replay-approvals${toQuery(params)}`);
}

export function approveReplayApproval(id: string): Promise<ReplayResponse> {
  return request(`/admin/v1/replay-approvals/${id}/approve`, { method: "POST" });
}

export function rejectReplayApproval(
  id: string,
  body: { reason: string },
): Promise<{ approval_id: string; status: ReplayApprovalStatus }> {
  return request(`/admin/v1/replay-approvals/${id}/reject`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function sandboxTestDelivery(body: {
  partner_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  idempotency_key?: string;
}): Promise<SandboxTestResponse> {
  return request("/admin/v1/deliveries/test", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

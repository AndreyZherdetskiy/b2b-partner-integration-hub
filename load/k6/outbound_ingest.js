/**
 * k6 load script: POST /internal/v1/outbound/events (persist path).
 * Measures POST → expected HTTP 202 (accepted), not end-to-end partner delivery SLA.
 */
import { check, fail } from "k6";
import http from "k6/http";

const BASE = __ENV.BASE || "http://127.0.0.1:8000";
const ADMIN_TOKEN = __ENV.ADMIN_TOKEN || "";
const PARTNER_PUBLIC_ID = __ENV.K6_PARTNER_PUBLIC_ID || "";
const EVENT_TYPE = __ENV.K6_EVENT_TYPE || "order.created";

export const options = {
  vus: Number(__ENV.K6_VUS || 10),
  duration: __ENV.K6_DURATION || "30s",
  thresholds: {
  // POST → 202 accepted; not a contractual SLA or 2M/day claim.
    "http_req_duration{expected_response:true}": ["p(95)<2000"],
    checks: ["rate>0.99"],
  },
};

function uuidv7() {
  const unixMs = Date.now();
  const bytes = new Uint8Array(16);

  bytes[0] = (unixMs / 2 ** 40) & 0xff;
  bytes[1] = (unixMs / 2 ** 32) & 0xff;
  bytes[2] = (unixMs / 2 ** 24) & 0xff;
  bytes[3] = (unixMs / 2 ** 16) & 0xff;
  bytes[4] = (unixMs / 2 ** 8) & 0xff;
  bytes[5] = unixMs & 0xff;

  for (let i = 6; i < 16; i += 1) {
    bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x70;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;

  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function setup() {
  if (!PARTNER_PUBLIC_ID) {
    fail(
      "K6_PARTNER_PUBLIC_ID is required. After make seed, read partner public_id from " +
        "GET /admin/v1/partners (Authorization: Bearer ADMIN_BOOTSTRAP_TOKEN) or seed output.",
    );
  }
  if (!ADMIN_TOKEN) {
    fail(
      "ADMIN_TOKEN is required. Set to the same value as ADMIN_BOOTSTRAP_TOKEN in .env / Compose.",
    );
  }
  return { partnerId: PARTNER_PUBLIC_ID };
}

export default function (data) {
  const idempotencyKey = `k6-${__VU}-${__ITER}-${Date.now()}`;
  const body = JSON.stringify({
    partner_id: data.partnerId,
    event_type: EVENT_TYPE,
    payload: { order_id: `k6-${idempotencyKey}`, amount: 1.0 },
    idempotency_key: idempotencyKey,
    correlation_id: uuidv7(),
  });

  const res = http.post(`${BASE}/internal/v1/outbound/events`, body, {
    headers: {
      Authorization: `Bearer ${ADMIN_TOKEN}`,
      "Content-Type": "application/json",
    },
  });

  check(res, {
    "status is 202 accepted": (r) => r.status === 202,
  });
}

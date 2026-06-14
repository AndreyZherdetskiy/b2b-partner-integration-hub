# Outbound ingest load test (k6)

## What this measures

`POST /internal/v1/outbound/events` with admin auth. Success = HTTP **202** (`status: accepted`). The k6 threshold `http_req_duration{expected_response:true} p(95)<2000` is a **local regression guard** on the persist path (DB + transactional outbox row), not:

- partner webhook round-trip time
- Kafka consumer lag to `delivered`
- a contractual SLA or a validated **2M deliveries/day** capacity claim

## Prerequisites

1. Stack up with API: `make compose-up` (full Compose project `b2b-partner-integration-hub`) or run API locally against Postgres/Redis/Kafka.
2. Seed data: `make migrate && make seed`.
3. Partner `public_id` for an active partner with an outbound endpoint subscribed to `order.created` (canonical seed: `acme-erp`). Obtain via Admin API or seed script output.
4. Admin token: same value as `ADMIN_BOOTSTRAP_TOKEN` in `.env` / Compose (demo default in `.env.example`).

## Run (Docker, host network)

From the repo root:

```bash
docker run --rm -i --network host \
  -e BASE=http://127.0.0.1:8000 \
  -e ADMIN_TOKEN=demo-admin-bootstrap-token-not-for-prod \
  -e K6_PARTNER_PUBLIC_ID=<partner-public-uuidv7> \
  grafana/k6 run - < load/k6/outbound_ingest.js
```

Equivalent Make target (requires `K6_PARTNER_PUBLIC_ID`; passes `ADMIN_BOOTSTRAP_TOKEN` as `ADMIN_TOKEN` when set):

```bash
export K6_PARTNER_PUBLIC_ID=<partner-public-uuidv7>
export ADMIN_BOOTSTRAP_TOKEN=demo-admin-bootstrap-token-not-for-prod
make load-k6
```

On Compose **internal** network (from a machine that can reach `hub-api:8000`), set `BASE=http://hub-api:8000` and omit `--network host` if you attach the k6 container to the Compose network.

Optional tuning: `K6_VUS` (default 10), `K6_DURATION` (default `30s`), `K6_EVENT_TYPE` (default `order.created`).

## Script

`load/k6/outbound_ingest.js` — unique `idempotency_key` per VU/iteration, UUIDv7 `correlation_id`, Bearer admin token.

## Last run

| Date | VUs | Duration | p95 (ms) `http_req_duration{expected_response:true}` | Pass `p(95)<2000` |
|------|-----|----------|------------------------------------------------------|-------------------|
| 2026-06-02 | 2 | 10s | 40.88 | yes (818/818 HTTP 202) |
| 2026-06-13 | 2 | 10s | 11.82 | yes (2252/2252 HTTP 202; overlay after persist CTE; k6 via stdin — WSL bind-mount of `make load-k6` failed) |

Command:

```bash
export K6_PARTNER_PUBLIC_ID=<acme-erp public_id>
export ADMIN_BOOTSTRAP_TOKEN=demo-admin-bootstrap-token-not-for-prod
export K6_VUS=2
export K6_DURATION=10s
make load-k6
```

This is a POST→202 persist-path regression, not an SLA and not a 2M/day claim.

# ADR-006: Thin admin UI over Admin API

- **Status:** Accepted
- **Date:** 2026-06-01
- **Spec:** §4.12, §12.6, §14

## Context

Support/SRE need to find a delivery and replay it in minutes. A Partner Portal is out of scope. Curl-only demos hide the status machine.

## Decision

`admin_ui/` is a **Vite + React + TypeScript** SPA. It calls **only** `/admin/v1/*`. It must not implement retry, HMAC, outbox, circuit breaker, or Kafka. Replay is disabled until `reason` is non-empty. Payload is masked/truncated. Delivery status switches are exhaustive (`never` default).

Stage 1 screens: deliveries list, delivery detail + attempts, DLQ list, partners list. Stage 2: filters, bulk replay, partner compliance.

## Consequences

- Typed client from OpenAPI or a thin `client.ts` — no duplicated state machine.
- UI container on host port 8080; CORS allow `http://localhost:8080` in demo only.

## Alternatives considered

- HTMX / server-rendered HTML — not the chosen stack for this product.
- Full Partner Portal — out of scope.

## Links

- Spec §14
- ADR-009 (copy public id, never BIGINT)

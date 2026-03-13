# ADR-005: Per-partner circuit breaker in Redis

- **Status:** Accepted
- **Date:** 2026-03-12
- **Spec:** §4.8, §12.5

## Context

A down partner plus unbounded retries creates a retry storm: we burn SLA budget and hammer their endpoint.

## Decision

Circuit breaker **per partner** in Redis: closed → open after `failure_threshold` failures in `window_seconds` → `open_duration` pause → half-open probe. Defaults: 10 / 60s / 300s (Appendix A).

**Stage 2 Must.** Stage 1 may omit CB as long as retries still cap at `max_attempts` and poison still DLQs.

**Redis down:** **fail-open** outbound (attempt delivery; do not fail closed the whole hub). Inbound idempotency falls back to PostgreSQL UNIQUE `(partner_id, idempotency_key)`. Document this in runbooks.

## Consequences

- Gauge `hub_circuit_breaker_state` uses `partner_slug` + `state`, never UUID labels.
- Bulk replay must respect open circuits (Stage 2).

## Alternatives considered

- Global rate limit only — punishes healthy partners.
- Fail-closed on Redis loss — turns a cache outage into a delivery outage.

## Links

- Spec §12.5, Appendix A
- ADR-002

# ADR-001: At-least-once delivery, not exactly-once

- **Status:** Accepted
- **Date:** 2026-03-11
- **Spec:** §4.9, §12.1

## Context

Stakeholders ask for “guaranteed delivery without duplicates.” HTTP webhooks cannot provide end-to-end exactly-once without the receiver’s cooperation (timeouts after success, URL changes, at-least-once Kafka).

## Decision

Delivery semantics are **at-least-once**. Partners **must** deduplicate on `Idempotency-Key`. The hub may POST the same payload more than once (retries, replay). Replay uses the **same** payload and the **same** idempotency key.

## Consequences

- Duplicate HTTP deliveries are possible and expected.
- OpenAPI and partner docs state the idempotency contract.
- Exactly-once claims in README, metrics, or sales claims are forbidden.

## Alternatives considered

- Kafka transactions + EOS inside the cluster — does not extend to partner HTTP.
- Hub-only dedup without partner participation — still fails on timeout-after-2xx.

## Links

- Spec §12.1
- ADR-002 (retries), ADR-007 (outbox), ADR-004 (HMAC)

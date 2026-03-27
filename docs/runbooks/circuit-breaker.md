# Runbook: Circuit breaker open

**Alert:** `HubCircuitOpen`
**Spec:** §8.5.4, ADR-005

## Symptoms

- `hub_circuit_breaker_state{state="open"}` equals 2 for a `partner_slug`
- Outbound deliveries skip POST and schedule retry while open
- DLQ or SLA breach alerts may follow if the partner stays unhealthy

## Checks

1. Grafana **SLA and Compliance** panel: circuit open gauge for the partner.
2. Admin UI deliveries: rising `retrying` / `failed` for that slug.
3. Partner mock or real endpoint: 5xx, timeout, or sustained error rate.
4. Redis connectivity: circuit state is stored per partner in Redis.

## Safe actions

- **Redis down (fail-open):** outbound continues without circuit protection (ADR-005). Restore Redis before relying on breaker semantics; DB UNIQUE still guards inbound idempotency.
- **Partner outage:** wait for recovery; do not bulk-replay while the circuit is open.
- **Pause bulk-replay** for the affected slug until `hub_circuit_breaker_state` returns to closed (0).
- After recovery, controlled replay with rate limit and audit `reason`.

## Escalation

BizDev + SRE if a tier-1 partner stays open > 15m or DLQ age also breaches.

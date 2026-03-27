# Runbook: DLQ growth

**Alert:** `HubDLQGrowth` / `HubDLQAge`
**Spec:** Appendix C

## Symptoms

- `rate(hub_dlq_messages_total[5m])` above threshold
- `hub_dlq_oldest_age_seconds` > 3600
- Grafana DLQ panel rising; Admin UI dead-letter list growing

## Checks

1. Open Admin UI Dead letters or `GET /admin/v1/dead-letters`.
2. Read `last_http_status` / `reason` (`max_attempts_exceeded` vs `non_retryable_error`).
3. Classify:
   - **Poison:** 400/401/403/404/422 — same payload will not succeed.
   - **Outage:** 5xx/timeout/circuit open — partner or network.

## Safe actions

- Poison: file a domain/contract ticket. **Do not** auto-replay.
- Outage: wait for circuit closed / partner recovery; replay with `reason` and rate limit.
- Ack after successful replay or purge with audit reason (`hub_admin` only).

## Escalation

If unpublished outbox or Kafka lag is also high, treat as hub incident (not partner poison). See [`outbox-lag.md`](outbox-lag.md).

# Runbook: SLA breach

**Alert:** `HubComplianceDrop` / `hub_sla_breaches_total`
**Spec:** Appendix D, ADR-008

## Symptoms

- `hub_sla_compliance_ratio` below 98% for a partner
- Deliveries with `sla_breached=true`
- Kafka `hub.integration.sla_breached`

## Checks

1. Filter Admin deliveries `sla_breached=true` for the window.
2. Correlate with DLQ, circuit open, Kafka lag, outbox unpublished.
3. Clock is `first_success_at` vs `sla_deadline_at` — later replays do not rewrite first success.

## Safe actions

- Hub incident: restore workers/relay first; then controlled replay.
- Partner outage: communicate; replay after recovery with rate limit.
- Export facts for Finance/Legal. **Do not** compute money penalties in the hub.

## Escalation

BizDev + SRE if a tier-1 partner stays breached after recovery.

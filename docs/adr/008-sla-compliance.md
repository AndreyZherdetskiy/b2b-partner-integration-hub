# ADR-008: SLA compliance measurement in the hub

- **Status:** Accepted
- **Date:** 2026-06-01
- **Spec:** §1.4, §12.8

## Context

Partner contracts have delivery SLAs, but without timestamps the business argues from tickets. The hub must operationalize compliance without becoming a billing/penalty engine.

## Decision

- Each partner has `sla_seconds` (default 60); endpoints may override.
- On delivery create, snapshot `sla_deadline_at = created_at + sla_seconds`.
- SLA clock **stops** at `first_success_at` (first HTTP 2xx). Later replays do not move that timestamp.
- Set `sla_breached` **once** if `now > sla_deadline_at` at first success **or** when the deadline passes while the delivery is still not delivered. Publish `hub.integration.sla_breached` when the flag flips true.
- Metrics use `partner_slug` (spec table says `partner_id` — slug avoids high-cardinality UUIDs; noted in `docs/slo.md`).
- The hub **does not** compute money penalties.

Replay **must not** rewrite payload (spec §12.12).

## Consequences

- Finance/Legal consume facts, not calculated fines.
- Grafana “SLA & Compliance” is Stage 2 (Stage 1 may stub a panel).

## Alternatives considered

- External BI only — too late for alerts.
- Penalty math in the hub — out of scope.

## Links

- Spec §1.4, §12.8, §12.12
- ADR-001

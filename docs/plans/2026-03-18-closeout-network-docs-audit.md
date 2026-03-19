# Hub closeout — network, docs, audit

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.
> Implementer ≠ Reviewer. **Do not git commit.** Checklists `- [ ]`.

**Goal:** Compose network `b2b_partner_integration_hub`, secrets only in `.env`, SDD phase-prompt pack analog to sibling repos, EN operator docs current with **no gitignored-path links**, live restart + seed + tests + dataflow, final architecture audit report (no Stage Done).

**Architecture:** Same hub stack. Project Compose `name` + explicit default network name. Postgres volume explicitly named so the new project does not silently reuse a wrong volume.

**Tech Stack:** Existing Stage 3 hub. Python 3.12.

## Global Constraints

- Dual-id only on `partners` / `deliveries`; BIGINT never in DTO/OpenAPI/UI/Kafka.
- Kafka retries not Celery; aiokafka; message key = partner `public_id`.
- Transactional outbox; Celery maintenance only.
- HMAC-SHA256 raw body; Jaeger not Tempo.
- Tracked EN docs: operator English; no gitignored-path links.
- Unpublished local notes stay gitignored; do not link them from tracked markdown.
- No WSL / Windows host docs.
- Secrets live in `.env`; `.env.example` is the tracked template (demo placeholders only).
- Do not git commit/push.
- Compose stays 1 Kafka broker RF=1.

---

### Task 0: Compose project/network + secrets in `.env`

**Files:** `docker-compose.yml`, `docker-compose.test.yml`, `.env.example`, `.env` (local), `tests/unit/test_compose_ports.py`, `README.md` (network name), `Makefile` if needed.

Set:

```yaml
name: b2b_partner_integration_hub
networks:
  default:
    name: b2b_partner_integration_hub
volumes:
  postgres_data:
    name: b2b_partner_integration_hub_postgres_data
```

Postgres / Grafana credentials from `.env` (`POSTGRES_*`, `GF_SECURITY_ADMIN_*`). Hub-api Compose DSN may still override host→`postgres`. `HUB_REPLAY_APPROVAL_REQUIRED=true` remains a **Compose** override for `hub-api` only (host pytest uses Settings default False).

**Acceptance:** `docker network ls` shows `b2b_partner_integration_hub`; no hardcoded `POSTGRES_PASSWORD: hub` in compose; unit compose tests green.

**Do not commit.** Down the old project (`2_b2b_partner_integration_hub`) before up with the new name.

---

### Task 1: SDD prompts pack

**Files:** local gitignored SDD phase prompts — same harness pattern as sibling hub repos.

Index + COMMON + executed Stage 1–3 phase prompts (point at existing plans; status executed) + architecture-audit prompt + docs-audit prompt.

**Acceptance:** index lists all files; prompts are hub-specific (not billing ledger / OFOM saga / SSO PKCE).

---

### Task 2: Unpublished local notes (gitignored)

Self-contained unpublished notes covering hub invariants (HMAC, Kafka retries, outbox, dual-id, fan-out, CB, SLA clock, thin UI). Not linked from tracked EN.

**Acceptance:** notes stay gitignored; no required reading paths from README / AGENTS / `docs/`.

---

### Task 3: EN docs currency + no gitignore links

README, AGENTS, CONTRIBUTING, `docs/**` — accurate ports, network name, runbooks, Python 3.12. Historical plans: reword evidence paths to “local gitignored SDD evidence” without gitignored-path tokens.

---

### Task 4: Restart, logs, seed, tests, dataflow, audit

`docker compose up -d --build`; migrate; seed; `make ci`; manual dataflow (happy outbound, 400→DLQ, HMAC 403, duplicate 200, sandbox); logs; write local gitignored architecture audit dated 2026-03-18. Do not write Stage Done.

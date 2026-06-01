# Role: Docs

Write / update **English** prod-like documentation for git: ADR, runbooks, README, CONTRIBUTING, slo, architecture, agentic guides, and the entry point [`AGENTS.md`](../../../AGENTS.md).

Tracked `docs/` is English prose; code/path/API identifiers stay as-is. Do not duplicate long-form text in `AGENTS.md` — keep navigation there and detail in `docs/`.

## Rules

- Do not contradict `spec.md` or Accepted ADR.
- Code / path / API identifiers — English as-is; prose in tracked docs **and** `spec.md` — **English**.
- Later-stage roadmap — only with a “roadmap only” label, never as mandatory current-task scope.
- Runbooks: symptoms → checks → safe actions → escalation. Follow spec Appendix B/C/D: classify poison vs outage; never auto-replay poison; rate-limit bulk replay.
- Do not commit secrets; `.env.example` is the single env template at repo root.
- DoD and acceptance checklists reference `spec.md` §11 (active stage); do not invent parallel criteria.
- **Entry-point sync:** any change under `docs/` in the same task checks and updates matching `AGENTS.md` sections. Without that, the task is not complete — see `AGENTS.md` §0.3.
- Do not markdown-link any path listed in `.gitignore`.
- OpenAPI snapshot README may **only** point at `/docs`, `/redoc`, `/openapi.json`, and the committed snapshot. **No** endpoint tables.
- Do not clone OpenAPI into markdown catalogs.

Update `.superpowers/sdd/progress.md` after Tasks (no Task N in application code).

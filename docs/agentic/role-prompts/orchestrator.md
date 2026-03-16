# Role: Orchestrator

You coordinate task execution against the active-stage plan. **Entry point:** [`AGENTS.md`](../../../AGENTS.md). Product: [`spec.md`](../../../spec.md). Phase contract: [`AGENTS.md` §10.1](../../../AGENTS.md#101-common-entry). Details — the [`docs/`](../../) tree per the map in `AGENTS.md` §0.

## Do

- Start from `AGENTS.md` (§0–9); when changing anything under `docs/`, sync `AGENTS.md` (§0.3) before the task is complete.
- Own Task order, progress ledger (`.superpowers/sdd/progress.md`), evidence files.
- Per Task — a **fresh** Implementer subagent with a self-contained prompt (Files, Spec §§, Acceptance, Global Constraints).
- After implementation — a **separate** Reviewer (Implementer ≠ Reviewer). Self-APPROVE forbidden.
- As needed — Grounding, Security, Fix → re-review.
- Before claiming a task / phase complete — `verification-before-completion` (fresh commands + output).
- Local-only: no `git push` / `gh` mutations / remote deploy without an explicit human command.
- Commits — only if the human explicitly asked. Do not use finishing-a-development-branch / new-branch-and-pr unless asked.
- Subagent models: `composer-2.5` or `cursor-grok-4.5-high` only.

## Do not

- Write the full domain implementation yourself bypassing subagent-driven mode (exception: trivial 1–2 file fix after REQUEST CHANGES; Reviewer still separate).
- Rewrite Accepted ADR / Spec “for beauty”.
- Pull scope from neighboring Tasks or later stages without a roadmap-only label.
- Declare a stage Done; write evidence and continue.
- Copy OFOM / billing / SSO product invariants into this hub.

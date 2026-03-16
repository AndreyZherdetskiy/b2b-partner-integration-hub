# Skills map — Partner Integration Hub

Skills / subagents map. **Entry point:** [`AGENTS.md`](../../AGENTS.md). Product: [`spec.md`](../../spec.md).
Phases and execution contract: [`AGENTS.md` §10](../../AGENTS.md#10-stage-development-supplement).

| Task type | Skill / mechanism | Cursor Task |
|-----------|-------------------|-------------|
| Write / refine a plan | `superpowers:writing-plans` | parent agent; save `docs/plans/YYYY-MM-DD-*.md`; strip commit steps |
| Product forks before code | `superpowers:brainstorming` | parent; spec already decided — do not invent SOAP/Portal/mesh |
| Execute plan task-by-task | `superpowers:subagent-driven-development` | Orchestrator + subagents; **no** finishing-branch/PR |
| Implement Task N | — | `generalPurpose` (Implementer), model `composer-2.5` |
| Architecture / Stage plans / whole-branch review | — | `generalPurpose`, model `cursor-grok-4.5-high` |
| Narrow shell / git / compose commands | — | `shell` |
| Search code / Spec | — | `explore` (quick / medium / very thorough) |
| Review a task (Gates A–D) | `superpowers:requesting-code-review` (adapted) | `generalPurpose` Reviewer ≠ Implementer |
| Bugbot-like review | — | `bugbot` (only on explicit request) |
| Security (HMAC, keys, Fernet, RBAC) | role-prompt security | `generalPurpose` (Security) |
| Red test / bug | `superpowers:systematic-debugging` | `generalPurpose` or `shell` |
| Docs-grounding (official docs §5) | — | `generalPurpose` + WebFetch / WebSearch |
| ADR / AGENTS / runbooks / slo | — | `generalPurpose` (Docs) |
| Before “done” | `superpowers:verification-before-completion` | Orchestrator |
| Feature isolation | `superpowers:using-git-worktrees` | on human request |
| Finish a branch | `superpowers:finishing-a-development-branch` | **only on explicit human request**; no push by default |

## Roles → prompts

| Role | File |
|------|------|
| Orchestrator | [`role-prompts/orchestrator.md`](role-prompts/orchestrator.md) |
| Implementer | [`role-prompts/implementer.md`](role-prompts/implementer.md) |
| Reviewer | [`role-prompts/reviewer.md`](role-prompts/reviewer.md) |
| Grounding | [`role-prompts/grounding.md`](role-prompts/grounding.md) |
| Security | [`role-prompts/security.md`](role-prompts/security.md) |
| Docs | [`role-prompts/docs.md`](role-prompts/docs.md) |
| Test | [`role-prompts/test.md`](role-prompts/test.md) |

## Parallelism

Allowed **only** when the plan explicitly marks independent tracks with a sync point. Never two implementers on the same paths.

## Subagent models

Cursor built-in models only: `composer-2.5`, `cursor-grok-4.5-high`. Never `composer-2.5-fast` / `*-fast`. Never BYOK slugs. Implementer and Reviewer are separate Task invocations.

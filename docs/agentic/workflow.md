# Agentic workflow (local)

Task execution loop for local agentic development.

**Entry point:** [`AGENTS.md`](../../AGENTS.md) (required; `docs/` map and sync — §0).
Product source of truth: [`spec.md`](../../spec.md) v3.1 EN.

**Phases and prompts:** via [`AGENTS.md` §10](../../AGENTS.md#10-stage-development-supplement) only.

```text
plan → TDD (failing test) → implement → review (Gates A–D)
    → [grounding | security as required] → verify → progress/report
```

Orchestration contract, skills, stop conditions, and report format live in `AGENTS.md`. Do not fork conflicting copies.

## 1. Plan

- Task source: active `docs/plans/*-implementation-plan.md`.
- Stage entry: active-stage phases via [`AGENTS.md` §10](../../AGENTS.md#10-stage-development-supplement).
- Orchestrator owns the Task N checklist; does not write domain code when subagent-driven mode is on (exception: 1–2 file fix after REQUEST CHANGES; Reviewer still separate).
- Skill: `superpowers:writing-plans` (create plan; **no commit steps**); execution — `subagent-driven-development` (no finishing-branch/PR unless the human asks).

## 2. TDD

1. Implementer writes a **failing** test (contract from the task).
2. Runs the exact command — expect FAIL (evidence).
3. Minimal implementation.
4. Same test — PASS (evidence).
5. On red after a “fix” — `superpowers:systematic-debugging`, not guesswork.

## 3. Implement

- Fresh Task subagent `generalPurpose` per task (`composer-2.5`; `cursor-grok-4.5-high` for architecture).
- Self-contained prompt: Files paths, Interfaces, Spec §§, Global Constraints, Acceptance.
- Commit only if the human asked.
- Local-only: no push / gh / remote deploy without an explicit command.

## 4. Review (Implementer ≠ Reviewer)

Separate subagent as Reviewer. Gates — `AGENTS.md` §6 / `role-prompts/reviewer.md`.

Verdict: **APPROVE** | **REQUEST CHANGES**. On REQUEST CHANGES — fix → re-review. Self-APPROVE forbidden.

## 5. Docs-grounding

For plan patterns (SQLAlchemy async, Alembic, FastAPI lifespan/OpenAPI, HMAC, aiokafka, Redis, Celery vs Kafka retries, OTel Collector, httpx timeouts):

1. Grounding compares implementation to official docs for major versions in Spec §5.
2. **Sources consulted** field in the task report.
3. Spec↔docs conflict: product invariants from Spec; library API from docs; trade-off → ADR.

## 6. Verify

Before declaring a task / phase complete — `superpowers:verification-before-completion`: fresh local commands and their output, not “should work”.

Do **not** announce “Stage N Done”. Write a local gitignored Stage N DoD evidence file and continue.

## Human checkpoints

Do not ask “continue?” between ordinary in-phase tasks.
Stop is mandatory on BLOCKED / security stop-the-line / secrets leaking into git.

## Progress

After each Task:

- `.superpowers/sdd/progress.md` — status / review / notes (gitignored local harness);
- local gitignored SDD progress ledger;
- plan Step checklists `- [x]`.

# AWF 1.9.2 change request: make the rehearsal path work on a real Codex host

**Date:** 23 September 2026
**Raised by:** the signal-lab showcase rehearsal (slides 10, 11 and 14 of the 1.9.1 showcase deck)
**Base:** `jkinlay/agentic-workflow-framework` `main` (release 1.9.1 source; the release worktree pin is `c3bed1ce…d564d25`)
**Type:** minor release. No weakening of any review floor, boundary, merge rule or reconciliation rule.

## Why

Running ticket SL-1 on signal-lab with Codex CLI 0.153.4 on Windows exposed four gaps. Every control behaved as designed, but a real host could not satisfy them:

1. `route_model.py reserve` refused the worker with "cost ceiling is configured but a verified hard cost reservation is unavailable". `execution.max_cost_microusd_per_ticket` and `execution.daily_project_cost_microusd` are required integers in the schema, so every project carries a dollar cap. Codex CLI reports tokens, not dollars, so no run can ever be admitted. The shipped token budget (100,000 per ticket) is also below one real run: SL-1 used 97,767 worker tokens and 185,208 critic tokens.
2. The worker's `workspace-write` sandbox could not create `index.lock` or `HEAD.lock` in the worktree's git metadata, so it could not commit. It correctly reported BLOCKED; the publisher committed the unchanged worktree. Nothing in the lifecycle or prompts describes this route.
3. After adoption, signal-lab CI failed lint with 3,218 errors, all inside AWF-managed `.agentic/` files, because the project's ruff configuration covered them. The managed files cannot be edited, so the fix has to be a project-side exclusion, and nothing warned about it.
4. The 1.9.1 portable `$awf` skill cannot be built from the public repository: `scripts/build_skill_distribution.py --skill-source` needs a portable skill source containing `scripts/install_skill.py`, which only exists in the 1.7–1.8.9 distributions.

## Changes

### A. Token-only budgets

- Schema: `execution.max_cost_microusd_per_ticket` and `execution.daily_project_cost_microusd` accept `null`, meaning "no monetary ceiling". Integers keep their current meaning and minimum.
- Routing: when every monetary ceiling in the effective budgets is `null`, `reserve` admits a run with `reservation_cost_microusd: null`, and `settle` records actual tokens with `actual_cost_microusd: null`. Token ceilings, run ceilings, duplicate-admission guards, quarantine on overrun and all other checks are unchanged. When any monetary ceiling is an integer, behaviour is exactly as in 1.9.1.
- Defaults for **new** adoptions: both monetary ceilings `null`; `execution.max_tokens_per_ticket` and `model_routing.budgets.max_tokens_per_ticket` 1,000,000; `model_routing.budgets.max_tokens_per_project_day` 5,000,000. Existing project configuration is preserved on upgrade; the migration note explains how an owner opts in through a reviewed governance PR.
- Documentation: `27-MODEL-ROUTING.md` gains a short "Token-only hosts" paragraph.

### B. Publisher commit when the worker sandbox cannot write git metadata

- Lifecycle doc, worker prompt and `AGENTS.md` (managed text): a worker whose sandbox denies writes to git metadata leaves the change uncommitted, runs validation, reports BLOCKED only for the commit, and records the tested tree. The assigned publisher then commits exactly that worktree (no content changes), verifies the committed tree equals the tested tree, pushes and opens the draft PR. Any difference between tested and committed content is a scope violation, not a publisher fix.
- `worker-result` gains optional `commit_route` (`WORKER` or `PUBLISHER`); absent means `WORKER`.

### C. Lint-scope preflight row

- `workflow.py preflight` and bootstrap's host preflight gain a row `project_lint_scope`: WARN when the project root `pyproject.toml` configures ruff (or `setup.cfg`/`.flake8` configures flake8) without excluding `.agentic`; PASS when excluded; N_A when no linter configuration is found. Remedy text: add `extend-exclude = [".agentic"]` (ruff) or the flake8 equivalent. The row never blocks INSTALLED, like the others.
- Adoption PR template mentions the row.

### D. Package the portable skill

- Add the portable skill source to the repository under `global/awf-portable/`, ported from the 1.8.9 distribution's `awf/` folder (without `assets/`), updated to 1.9.2 and within its word budgets.
- `scripts/build_skill_distribution.py --skill-source global/awf-portable --output-dir NEW_DIR` must produce `AWF-v1.9.2-distribution` containing `install_awf.py` and a working `awf/scripts/install_skill.py`, with its tests passing.

### E. Release housekeeping

- Version 1.9.2 everywhere the release checks require it; `CHANGELOG.md`; `MIGRATION-v1.9.1-to-v1.9.2.md`; regenerated contracts, prompts, examples and `MANIFEST.json`/`MANIFEST.md`.
- Do not modify `docs/` showcase files.

## Acceptance criteria

| ID | Criterion | Validation |
| --- | --- | --- |
| AC1 | With both monetary ceilings `null`, `reserve` admits a worker request with `reservation_cost_microusd: null` and `settle` records it. | New unit tests in `test_model_routing.py` / `test_routing_cli.py` |
| AC2 | With a monetary ceiling set, behaviour is identical to 1.9.1, including the refusal message. | Existing tests unchanged and passing, plus one explicit regression test |
| AC3 | Token-ceiling overrun still quarantines; duplicate admission still refused. | Existing tests pass |
| AC4 | New-project bootstrap writes the new defaults. On upgrade from 1.9.1, every owner-set value (budgets included) is preserved exactly; the only permitted change is `template.expected_workflow_version` 1.9.1 → 1.9.2. *Amended 23 Sep 2026 by the owner after critic finding C2-F01: literal byte-for-byte preservation conflicts with the 1.9.2 schema.* | Upgrade test from a real 1.9.1 configuration with non-default budgets, asserting the byte diff is exactly that one line |
| AC5 | Worker prompt, lifecycle doc and AGENTS.md describe the publisher-commit route; `worker-result` schema accepts `commit_route`. | Schema test; prompt regeneration check |
| AC6 | `project_lint_scope` row reports WARN / PASS / N_A correctly. | Unit tests with fixture `pyproject.toml` files |
| AC7 | The portable distribution builds from `global/awf-portable` and its tests pass. | Build into a temp dir; run the skill tests |
| AC8 | Full `python -B scripts/self_test.py` passes; manifest regenerated; word budgets met. | Self-test report |

**Boundaries.** No change to review floors, critic independence, final-gate evidence, merge authorization, reconciliation or Jira rules. No weakening of any existing test. `docs/` untouched.

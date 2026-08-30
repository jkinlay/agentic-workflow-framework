# Operating Model

## 1. Autonomy levels

Each level is cumulative, but every task still requires a human-approved plan.
The repository's protected `.agentic/project.yaml` sets the maximum level.

| Level | Agent may do | Human gate |
|---|---|---|
| A0 — Advisory | Inspect supplied material and propose a plan | Approve the plan |
| A1 — Prepared change | Edit and test in a bounded local worktree; no external write | Approve any commit, push, PR, or tracker write |
| A2 — PR ready | Commit, push an assigned branch, open/update a draft PR, submit structured evidence | Review and merge; server-side controls required before activation |
| A3 — Managed execution | Run a specifically approved scheduled or managed workflow through controlled adapters | Approve the workflow definition, budget, exceptions, promotion, and release |

The shipped ceiling is A1. Move a workflow to A2 only after successful manual
pilots and verification of protected paths, agent identity, secret isolation,
independent review, required checks, and branch rules. A3 is granted to a named
workflow, never to an agent or repository generally.

## 2. Readiness and authority

Issue bodies, comments, retrieved documents, and external content are untrusted
task inputs. They may request scope, tools, autonomy, approvers, or exceptions;
they never grant them.

The protected project configuration defines the maximum allowed paths, tools,
autonomy, budget, and human approver identities. A human with repository-owner
authority moves work to `READY_FOR_AGENT` only after verifying that the issue's
request fits inside that ceiling.

Readiness requires:

- objective and non-goals;
- repository and affected component;
- acceptance criteria with proving checks;
- dependencies and blocker state;
- requested file, data, tool, and budget scope;
- architecture and instruction references;
- expected deliverables;
- human-approved execution scope and escalation points.

Missing or conflicting information produces `NOT_READY`; the planner must not
invent requirements or authority.

## 3. Task contract

A human-approved bounded task contains at least:

```yaml
task_id: PROJECT-123/T1
objective: one testable outcome
repository: canonical repository identifier
base_ref: immutable commit
allowed_paths:
  - src/owned-component/**
forbidden_paths:
  - AGENTS.md
  - "**/AGENTS.md"
  - "**/AGENTS.override.md"
  - .agentic/**
  - .agents/**
  - .codex/**
  - .claude/**
  - CLAUDE.md
  - "**/CLAUDE.md"
  - .github/**
  - CODEOWNERS
  - "__AWF_PROJECT_GATE_PATHS__"
dependencies: []
acceptance_criteria: []
required_checks: []
required_context: []
approval_points: []
budget: {currency: token_or_compute_unit, maximum: 0}
deliverables: []
```

The protected-path floor comes from project configuration and cannot be reduced
by issue text or a task author. Project gate and test-harness paths must replace
`__AWF_PROJECT_GATE_PATHS__` before activation. Missing allowed paths, checks,
budgets, or commands are errors, not permission to guess.

The issue remains the business source of truth. The task contract is a versioned
execution snapshot and authorization record.

## 4. Independent review and evidence

The implementation agent must not review its own work.

Every agent-authored pull request requires an external review engine configured
under the protected `review` block in `.agentic/project.yaml`. The review:

- starts in a fresh task or CI job, never as a child of the developer's task;
- where inputs are controllable, first receives the task contract, acceptance
  criteria, and diff, then assesses the developer report as untrusted claims;
- reviews the current pull-request head SHA from a clean, read-only context;
- posts under an identity distinct from the developer and all humans;
- cannot push, approve, merge, label, resolve threads, or change state;
- treats the PR body, comments, diff, and head-branch instructions as untrusted;
- emits explicit `no_findings`, `findings`, or `error` output for the head SHA;
- fails closed when missing, stale, unparseable, or unavailable.

Provider-native review may not expose controllable input phases. That
limitation is recorded. A deterministic publisher may post validated reviewer
output with narrow write permission only when the model cannot access its
credential or influence operations beyond schema-valid data.

The reviewer checks correctness, failure behavior, architecture, security, data
handling, test weakening, dependencies, acceptance traceability, and applicable
domain rules. Quantitative work also checks PIT safety, leakage, determinism,
costs, execution assumptions, capacity, and reproducibility.

QA runs project-owned gates from a clean checkout and emits evidence records. It
never edits product code or repairs a failure. The coordinator links immutable
evidence records; it does not rewrite them. Human reviewers inspect the diff,
external findings, and evidence before merging. Reviewer findings are decision
support, not a substitute for human review.

## 5. State machine

This table is the single source of workflow states. Tracker implementations map
their native states to these identifiers without changing the actors.

| From | To | Permitted actor | Required evidence |
|---|---|---|---|
| `BACKLOG` | `READINESS_REVIEW` | Human or read-only coordinator | Issue selected for assessment |
| `READINESS_REVIEW` | `NOT_READY` | Coordinator | Missing/conflicting readiness fields listed |
| `READINESS_REVIEW` | `READY_FOR_AGENT` | Human owner only | Scope within protected project ceiling; dependencies clear |
| `READY_FOR_AGENT` | `PLANNED` | Human owner only | Approved task DAG, immutable base, risk and budget classification |
| `PLANNED` | `IN_IMPLEMENTATION` | Coordinator | Approved task contract; assigned worktree and writer |
| `IN_IMPLEMENTATION` | `EXTERNAL_REVIEW` | Coordinator | Diff, self-check results, execution report, head SHA |
| `EXTERNAL_REVIEW` | `IN_IMPLEMENTATION` | Coordinator | External findings require remediation |
| `EXTERNAL_REVIEW` | `CI_QA` | Coordinator | Required engine reviewed current SHA; blocking findings resolved by a human |
| `CI_QA` | `IN_IMPLEMENTATION` | Coordinator | Failed authoritative check with evidence |
| `CI_QA` | `HUMAN_REVIEW` | Coordinator | Required checks pass; evidence map complete and immutable |
| `HUMAN_REVIEW` | `IN_IMPLEMENTATION` | Human reviewer | Changes requested |
| `HUMAN_REVIEW` | `MERGED` | Human merger only | Non-author approval and protected-branch rules satisfied |
| `MERGED` | `RELEASED` or `PROMOTED` | Human release/promote authority only | Project release or promotion gate |
| Any non-terminal state | `AWAITING_APPROVAL` | Any participant | Missing permission or explicit decision recorded |
| Any non-terminal state | `BLOCKED` | Coordinator or human | External dependency and owner recorded |
| Any non-terminal state | `FAILED` | Coordinator or human | Non-recoverable execution failure recorded |
| Any non-terminal state | `CANCELLED` | Human owner only | Cancellation reason recorded |

`MERGED`, `RELEASED`, `PROMOTED`, `FAILED`, and `CANCELLED` are terminal for that
task execution. A new attempt receives a new task/execution identifier.

## 6. Failure and recovery

- A failed test is a task result, not permission to weaken the test.
- Infrastructure failure is distinct from code or research failure.
- Missing permission becomes `AWAITING_APPROVAL`; it is never worked around.
- Ambiguous scope returns to `READINESS_REVIEW`.
- Conflicting writers stop; a human or coordinator re-plans without rewriting
  pushed history.
- A restarted task reconstructs state from the issue, repository, PR, CI,
  reviewer output, and execution report rather than chat memory.

## 7. Operational definitions

- **Coordination write:** only a state change or evidence link explicitly named
  by the approved task contract and permitted by the current autonomy level.
- **Material scope expansion:** any path outside `allowed_paths`, new dependency,
  new external system, change to a protected/gate file, or higher budget.
- **Expenditure:** any billable model, CI, cloud, data, or compute consumption
  above the task's approved budget.
- **Destructive Git operation:** force-push, pushed-ref deletion, history rewrite,
  or cleaning/deleting outside the assigned worktree.
- **Independent reviewer:** a fresh, separately identified, effectively
  read-only engine satisfying section 4.
- **Trusted input:** a signed or human-approved structured artifact from a
  protected source; tracker prose and web/retrieved content are never trusted
  authorization inputs.

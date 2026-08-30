# Implementation Plan

## Outcome

Establish a provider-neutral framework that can bootstrap a project repository
and support a reliable path from a well-specified issue to a reviewed,
evidence-backed pull request.

## Phase 0 — Distribution foundation

1. Publish AWF as the sanitized upstream framework.
2. Define bootstrap ownership classes and the version-lock contract.
3. Separate provider-neutral contracts from runtime adapters.
4. Establish confidentiality rules for personal and corporate instances.
5. Validate that the upstream contains no project-specific private material.

Exit criteria:

- distribution manifest is versioned and internally consistent;
- root governance files are distinct from project scaffolds;
- provider-specific behavior is isolated under adapter documentation;
- private paths, data, credentials, and project content are absent.

## Phase 1 — Contracts and manual bootstrap

Create and validate:

- project-profile schema;
- issue-readiness schema;
- task-brief schema;
- execution-report schema;
- review-finding schema;
- external review-report schema with immutable head SHA and reviewer identity;
- approval/action taxonomy;
- pull-request template with acceptance-evidence mapping.

Pilot an existing repository in this order:

1. read-only architecture and backlog assessment;
2. bootstrap proposal with no external writes;
3. one documentation or test-only task;
4. one bounded implementation task in a clean worktree.

Keep issue-tracker updates manual during the first pilots.

Before any developer identity receives push access, configure server-enforced
protected paths and keep agent-authored CI secret-less. Select and benchmark one
external reviewer (Copilot or the split Claude model/publisher adapter) and make
absence, failure, or staleness a blocking check.

Exit criteria:

- task briefs can be reconstructed without chat history;
- independent review catches seeded defects;
- the binding external review uses fresh, separate execution and identity,
  covers the current head SHA, and exposes no write credential to the model;
- every acceptance criterion has reviewable evidence;
- bootstrap preserves existing project instructions;
- no private or local-only content enters AWF.

## Phase 2 — Reusable workflows and read-only integrations

After the manual workflow is stable, create runtime-compatible procedures for:

- issue readiness assessment;
- task decomposition;
- implementation execution;
- independent code and quantitative-methodology review;
- QA evidence collection;
- pull-request preparation;
- backlog dependency audit;
- framework upgrade assessment.

Add read-only tracker and source-control adapters first. Verify authentication,
repository allowlists, field mappings, rate limits, audit logs, and untrusted-
content handling.

The reviewer model process is read-only and must not share a resumed developer
session. If a provider needs a write-capable publisher, it must be a separate,
deterministic non-model process with the minimum credential that the provider
supports. Provider availability and permission evidence are explicit project
configuration, not auto-detected secrets.

Exit criteria:

- workflows reproduce the manual process on fixed benchmark tasks;
- connector access is least-privilege and boundary-scoped;
- no write is required to perform planning or review.

## Phase 3 — Controlled writes and PR-ready autonomy

Enable narrowly scoped actions:

- create a branch/worktree;
- commit issue-scoped changes;
- push the assigned branch;
- open or update a draft PR;
- add a structured evidence comment;
- move an issue into an agent-review state.

Require human approval for merge, release, publication, and production/research
promotion.

Exit criteria:

- one issue maps to one branch and PR;
- protected-branch and CI gates cannot be bypassed;
- agent-authored pushes cannot modify protected execution/control paths or
  receive privileged CI secrets;
- every external write is attributable and reversible;
- recovery from a stopped task uses durable external state.

## Phase 4 — Controlled execution integration

For projects with research or managed-compute systems, integrate only through
their existing narrow waist:

- project and approval objects;
- controlled CLI or API contract;
- policy engine and execution gateway;
- manifests and artifact references;
- temporal integrity, determinism, leakage, and methodological validation;
- lineage-system links;
- independent reviewer and red-team workflows.

An agent runtime may plan and invoke approved capabilities. It must not become a
parallel compute, registry, or promotion plane.

Exit criteria:

- a bounded synthetic workflow replays from persisted state;
- infrastructure and domain failures are distinguished;
- deterministic policy blocks disallowed actions;
- promotion packages require independent and human review.

## Phase 5 — Scheduled operations

Schedule only proven, bounded workflows such as:

- stale dependency and issue-readiness audits;
- PR/CI status summaries;
- documentation drift checks;
- framework update assessments;
- approved run monitoring.

Use isolated worktrees for scheduled file changes and explicit stop/attention
conditions.

## Initial backlog

| ID | Work item | Priority |
|---|---|---|
| AWF-001 | Publish the provider-neutral distribution manifest | P0 |
| AWF-002 | Establish personal and corporate instance boundaries | P0 |
| AWF-003 | Define project-profile and task/evidence schemas | P0 |
| AWF-004 | Create issue-readiness and PR templates | P0 |
| AWF-005 | Specify bootstrap validation and reporting | P0 |
| AWF-006 | Specify upgrade diff and conflict handling | P1 |
| AWF-007 | Run a read-only existing-repository pilot | P1 |
| AWF-008 | Run a documentation/test bootstrap pilot | P1 |
| AWF-009 | Add provider-adapter conformance requirements | P1 |
| AWF-010 | Build a read-only issue-tracker adapter | P2 |
| AWF-011 | Build a source-control PR/check adapter | P2 |
| AWF-012 | Create and benchmark reusable workflows | P2 |
| AWF-013 | Automate bootstrap as a reviewable change | P2 |
| AWF-014 | Automate upgrades as reviewable changes | P2 |
| AWF-015 | Enable controlled draft-PR writes | P3 |
| AWF-016 | Pilot one controlled execution adapter | P3 |
| AWF-017 | Implement protected-path and current-head review gate checks | P0 |
| AWF-018 | Benchmark Copilot and/or Claude review on seeded defects | P0 |
| AWF-019 | Add identity, audit-retention, and incident-response contracts | P1 |
| AWF-020 | Implement and permission-test the split Claude model/publisher adapter | P1 |
| AWF-021 | Add manifest/project/lock schemas and provenance hash validation | P0 |
| AWF-022 | Add upstream CI, secret scanning, and review benchmark fixtures | P0 |

AWF-020 status at `24ecd82`: the scaffold implementation is merged, but live
qualification remains pending. Before activation, retain the task at P1 and
remediate the stateless claims-phase context, make the gate reject every false
qualification flag (including future flags), make the offline suite portable to
Windows, and remove or explicitly constrain the missing-policy Copilot fallback
before making `awf/review` required. Then run the same-repository permission
demonstration and seeded benchmark. Completion means the resulting evidence
supports all qualification flags; merged code alone is not completion.

## Success measures

- percentage of agent-started issues meeting readiness criteria;
- cycle time from ready to PR-ready;
- first-pass CI success rate;
- acceptance criteria with machine-verifiable evidence;
- reviewer defects found before human review;
- rework rate after human review;
- approval and blocked-action counts;
- task replay/recovery success;
- temporal-integrity and determinism failures caught before execution;
- zero confidentiality-boundary or credential incidents.

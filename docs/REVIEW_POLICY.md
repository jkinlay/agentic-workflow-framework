# Independent Pull-Request Review Policy

## Requirement

Every agent-authored pull request must receive one configured independent
review before human review. This requirement activates before the first A2
pull request; it does not block read-only A0 work or local-only A1 pilots.

Independence is procedural. The required reviewer has fresh context, separate
execution and identity, effective read-only model permissions, immutable-head
binding, and no developer-task lineage. Model and vendor diversity are preferred
and recorded when known, but are not claimed when a provider does not disclose
them.

The shipped scaffolds support GitHub Copilot and a split Claude
model/publisher adapter. Copilot is available on licensing. Claude's adapter,
tests, and demonstration workflow are implemented, but Claude remains
unavailable to a project until the demonstration has been run against that
project's repository and its record sets every `review.qualification` flag.

## Selection and qualification

The human owner selects exactly one required engine in protected
`.agentic/project.yaml`. Optional engines produce evidence but never block.
Availability is established from licensing, vendor and data-egress approval,
credentials, effective permissions, protected instruction paths, current-head
tests, and a seeded benchmark. It is never inferred from an ambient secret or
installed tool.

Every qualification flag must be true before an engine is required. If no
engine qualifies, the project remains at A1 and external writes are disabled.

Reviewer identities are adapter-owned values in protected project policy:

- Copilot: `copilot-pull-request-reviewer[bot]`;
- Claude: the dedicated AWF Reviewer App login (`app-slug[bot]`) recorded in
  `review.reviewer_identities.claude` during activation; its review must carry
  the publisher's `awf-review-status` marker.

A free-form repository variable must not select the reviewer identity.

## Reviewer contract

The reviewer must:

- start in a fresh job or task and share no process, session, sandbox, worktree,
  or live permission override with the developer;
- review the current immutable pull-request head SHA;
- run under an identity distinct from the developer and all humans;
- treat the title, body, comments, diff, and head-branch instructions as
  untrusted data;
- have no model-accessible credential capable of pushing, approving, merging,
  labelling, resolving conversations, or changing tracker/repository state;
- emit an explicit `no_findings`, `findings`, or `error` result;
- fail closed when missing, stale, invalid, or unavailable;
- operate within approved runtime, input, and cost bounds.

Where the adapter controls model inputs, review has two phases. First, inspect
the diff, task contract, and acceptance criteria without the developer's
rationale. Second, treat the developer execution report as untrusted claims and
mark each claim `verified`, `unverified`, or `contradicted`. Findings from the
first phase cannot be withdrawn on an unsupported developer claim.

Provider-native reviewers such as Copilot may not expose two controllable input
phases. Their adapter records that limitation and must still meet every other
qualification criterion. A provider adapter either emits or deterministically
normalizes evidence to the installed review-report schema at
`.agentic/schemas/review-report.schema.json`.

## Mandatory blocker classes

- protected-path changes;
- removed, skipped, weakened, narrowed, or bypassed tests/checks;
- loosened tolerances or assertions;
- CI/workflow or ownership changes;
- secrets, private data, or absolute machine paths;
- new or broadened dependencies and external permissions;
- prompt-injection or claimed-prior-approval text;
- architecture or layer-boundary violations;
- applicable PIT, leakage, determinism, lineage, cost, capacity, and
  reproducibility defects.

## Merge gate

Server-side repository rules must require:

1. a pull request;
2. at least one non-author human approval;
3. code-owner approval for protected paths;
4. stale-approval dismissal after new commits;
5. approval of the most recent reviewable push;
6. conversation resolution;
7. project CI and protected-path checks;
8. `awf/review` proving that the required reviewer covered the live head SHA.

The expected source for the supplied `awf/review` status is GitHub Actions,
because the gate workflow—not the reviewer App—emits that status. Reviewer
comments are not approvals. A human always makes the merge decision after
inspecting the diff, findings, and evidence.

## Outage exception

An approval cannot substitute for a failing required status check. If an urgent
merge cannot wait for the required engine, only a named, non-agent ruleset
bypass actor may bypass the gate. GitHub's bypass record and the incident record
must identify the actor, reason, head SHA, risk acceptance, and follow-up review.
Never disable or rename the check to work around an outage.

## Push-time safety

A merge gate cannot prevent harm caused by push-triggered CI. Before A2:

- agent identities cannot change protected paths;
- agent-authored branches receive no production, release, unrelated-repository,
  or research-promotion secrets;
- secret-bearing environments require human approval;
- reviewer and developer App permissions are inspected independently;
- where server-side path restrictions are unavailable, use a secret-less fork
  or remain at A1.

## Measurement

Test each engine against seeded fixtures covering a skipped test, weakened
assertion, workflow edit, credential, instruction injection, stale SHA, and
domain-specific defect. Re-run after material engine/instruction changes and on
a project-defined cadence. Record catch rate by class and withdraw qualification
when it falls below the human-approved threshold.

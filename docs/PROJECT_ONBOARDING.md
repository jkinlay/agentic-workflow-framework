# Project Onboarding Checklist

Do not add a project to the agent workflow until this checklist is complete.

## Identity and ownership

- Canonical project name and stable ID
- Product/research owner
- Canonical Git repository and default branch
- Issue-tracker project and workflow
- Confidentiality domain and approved agent environment
- Human approvers for merge, release, and production/research promotion
- Dedicated developer, external-reviewer, and human identities with auditable
  attribution
- `Task-Id` attribution convention and durable audit location/retention period
- Named incident stop authority and credential-revocation owners

## Repository readiness

- Root `README.md`
- Root `ARCHITECTURE.md`
- Root `AGENTS.md`
- Applicable nested `AGENTS.md` files
- Clean canonical base branch
- Reproducible setup and test commands
- CI with required checks
- Protected default branch
- Server-enforced protection for instructions, CI, ownership, reviewer, runtime,
  hooks, dependency execution, and `.agentic/project.yaml`
- Secret and large-file controls
- Push-triggered CI secret inventory; agent-authored branches receive no
  production, release, unrelated-repository, or research-promotion secrets
- Documented data and artifact locations
- AWF bootstrap mode selected: existing or greenfield repository
- AWF ownership policies reviewed against existing files
- `.agentic/workflow.lock.yaml` location approved

## Execution readiness

- Branch and PR naming convention
- Issue readiness and definition-of-done rules
- Allowed and forbidden paths/actions
- Protected path set is non-empty and derived from protected repository policy
- Test and evidence mapping
- External tools and least-privilege permissions
- Required external reviewer, immutable-head check, permission evidence, and
  seeded-defect benchmark
- Adapter-owned reviewer identity; the `awf/review` expected status source is
  the gate workflow's GitHub Actions App
- Named non-agent ruleset bypass actor and post-bypass incident procedure
- Worktree strategy
- Failure, rollback, and escalation procedure
- Audit retention and an incident response path for credential exposure,
  boundary crossing, or unauthorized external writes
- Identity and incident contracts reviewed against
  `docs/IDENTITY_AND_AUDIT.md` and `docs/INCIDENT_RESPONSE.md`
- Structured execution report

## Quantitative-research additions

- Point-in-time and availability semantics
- Leakage tests and temporal validation
- Deterministic seeds and dependency capture
- Data lineage and entitlement checks
- Transaction cost, spread, slippage, latency, and capacity assumptions
- Walk-forward/OOS policy
- Model, strategy, and artifact registry references
- Independent methodological review
- Research-to-production promotion gate

## Pilot sequence

1. Read-only repository and backlog assessment.
2. Documentation-only or test-only change.
3. Small code change with no external side effects.
4. After all A2 prerequisites pass, draft PR with independent review and CI
   evidence.
5. Controlled connector writes.
6. Scheduled or managed execution only after the earlier stages are stable.

## Bootstrap result

The onboarding change must record:

- AWF version;
- enabled runtime adapters;
- required external reviewer and its effective identity/permissions;
- qualification flags, stale-SHA result, expected status source, and outage
  bypass actor;
- managed, seed-once, merge-assisted, local-only, and generated artifacts;
- conflicts with existing project instructions;
- validation commands and results;
- required human decisions before activation.

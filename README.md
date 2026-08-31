# Agentic Workflow Framework

Agentic Workflow Framework (AWF) is a provider-neutral, human-governed toolkit
for applying agentic planning, implementation, review, and evidence workflows
to software and quantitative-research repositories.

AWF is an upstream distribution, not a product-code monorepo or execution
database. Each onboarded product repository becomes a project-local workflow
instance while retaining ownership of its code, architecture, instructions,
tests, and release controls.

Codex is the first supported runtime adapter. The core contracts deliberately
avoid depending on one model vendor or agent host.

## Operating model

- The issue tracker owns priorities, dependencies, approvals, and business
  status.
- Each product repository owns its code, `ARCHITECTURE.md`, `AGENTS.md`, tests,
  and CI configuration.
- Source control owns branches, pull requests, review state, and merge history.
- Runtime adapters perform bounded planning, implementation, review, and QA.
- Humans approve each execution plan and all actions above the project's
  configured autonomy ceiling. Humans always approve merges, releases, and
  production or live-research promotion.
- AWF owns shared contracts, scaffolds, schemas, provider adapters, and
  sanitized reusable workflows.
- Every agent-authored pull request receives an independent external-engine
  review before human review; reviewer output never replaces human inspection.

## Start here

1. Follow [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) to adopt AWF in a
   new or existing repository.
2. Read the integrated system description and diagrams in
   [docs/SYSTEM_ARCHITECTURE.md](docs/SYSTEM_ARCHITECTURE.md).
3. Read the authoritative decisions in [ARCHITECTURE.md](ARCHITECTURE.md).
4. Read [docs/OPERATING_MODEL.md](docs/OPERATING_MODEL.md).
5. Read the terminal-only [human merge handoff](docs/HUMAN_MERGE_HANDOFF.md).
6. Read [docs/REVIEW_POLICY.md](docs/REVIEW_POLICY.md).
7. Read [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).
8. Read [docs/BOOTSTRAPPING.md](docs/BOOTSTRAPPING.md).
9. Use [docs/PROJECT_ONBOARDING.md](docs/PROJECT_ONBOARDING.md) to assess a
   target repository.
10. Review provider-specific requirements under `docs/providers/`.

Before A2, also establish the identity/audit and incident-response contracts in
[docs/IDENTITY_AND_AUDIT.md](docs/IDENTITY_AND_AUDIT.md) and
[docs/INCIDENT_RESPONSE.md](docs/INCIDENT_RESPONSE.md).

## Distribution model

AWF files have explicit ownership policies:

- **managed**: AWF may update the file through a reviewable upgrade change;
- **seed-once**: AWF creates the initial file and the project then owns it;
- **merge-assisted**: AWF proposes amendments but never overwrites the project;
- **local-only**: the project creates and owns the file; AWF never collects it;
- **generated**: the installer derives the file and records installed hashes.

The canonical distribution policy is
[.agentic-workflow/distribution-manifest.yaml](.agentic-workflow/distribution-manifest.yaml).

## Current status

Version `0.1.1` is a documentation-first scaffold. Bootstrap and upgrade are
manual, reviewable procedures until their contracts have been piloted and are
stable enough to automate.
